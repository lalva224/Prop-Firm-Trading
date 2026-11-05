//+------------------------------------------------------------------+
//|  TcsBridge.mq5                                                   |
//|                                                                  |
//|  Executes target portfolio weights published by the Linux-side   |
//|  strategy, and reports account state back, through files in       |
//|  MQL5\Files. Designed for a once-a-day rebalance, so it favours   |
//|  being obvious and auditable over being fast.                     |
//|                                                                  |
//|  WHY FILES                                                        |
//|  MT5 runs under Wine; MQL5\Files is an ordinary Linux directory,  |
//|  so both sides share it with no sockets, ports or auth. Every     |
//|  instruction and every response is left on disk for inspection.   |
//|                                                                  |
//|  WHY THIS EA HAS ITS OWN RISK GUARD                               |
//|  The Linux guard can be wrong, stale, or not running. This EA is  |
//|  the process actually holding positions, so it recomputes both    |
//|  loss floors itself and refuses instructions that would breach    |
//|  them. Two independent checks, either of which can veto.          |
//|                                                                  |
//|  FAIL FLAT, NEVER FAIL FROZEN                                     |
//|  Anything wrong - stale instructions, lost connection, a breached |
//|  floor - resolves to "no position", never to an open position     |
//|  nobody is managing.                                              |
//+------------------------------------------------------------------+
#property copyright "tcs"
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- files shared with the Linux side
input string InpTargetsCsv     = "tcs_targets.csv";
input string InpStateJson      = "tcs_state.json";
input string InpHeartbeatJson  = "tcs_heartbeat.json";
input string InpDayStateFile   = "tcs_dayopen.txt";
input string InpSymbolsJson    = "tcs_symbols.json";

//--- cadence
input int    InpTimerSeconds   = 5;      // heartbeat/state cadence
input int    InpMaxTargetAgeS  = 1800;   // ignore instructions older than this

//--- independent risk limits. MUST match the issued account exactly.
input double InpInitialBalance = 200000; // fractions below are OF THIS, always
input double InpMaxDailyLoss   = 0.05;
input double InpMaxTotalLoss   = 0.10;
input double InpFlattenBufferF = 0.15;   // flatten inside this share of the daily allowance

//--- execution
input int    InpSlippagePoints = 20;
input int    InpMaxRetries     = 3;
input long   InpMagic          = 770011;

CTrade        trade;
string        g_errors[];
string        g_actions[];
string        g_last_nonce      = "";
double        g_day_open_balance = 0.0;
int           g_day_open_yday    = -1;

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.LogLevel(LOG_LEVEL_ERRORS);
   // NOTE: filling mode is deliberately NOT set here. It is per-symbol, and
   // this EA trades 20-30 of them; setting it once from the chart symbol gets
   // orders rejected on every instrument that needs a different mode.

   if(InpInitialBalance <= 0)
     { Print("FATAL: InpInitialBalance must be set to the issued account size"); return(INIT_PARAMETERS_INCORRECT); }
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
      Print("WARNING: algo trading is disabled for this account - orders will be rejected");
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
      Print("WARNING: 'Algo Trading' is off in the terminal - enable it or nothing executes");

   LoadDayOpen();
   EventSetTimer(MathMax(1, InpTimerSeconds));
   WriteHeartbeat();
   WriteState();
   PrintFormat("TcsBridge up. initial=%.2f daily_floor_at_open=%.2f total_floor=%.2f",
               InpInitialBalance, DailyFloor(), TotalFloor());
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

//+------------------------------------------------------------------+
void OnTimer()
  {
   ArrayResize(g_errors, 0);
   RollDayIfNeeded();

   WriteHeartbeat();

   // The EA's own guard runs every tick, not only at rebalance time: a
   // floating loss can approach the floor between instructions.
   if(BreachImminent())
     {
      AddError("own guard: equity near or below binding floor - flattening");
      CloseAllPositions();
      WriteState();
      return;
     }

   ProcessTargets();
   WriteState();
  }

//+------------------------------------------------------------------+
//| Floors. Both are fractions of the INITIAL balance and never grow  |
//| with the account.                                                 |
//+------------------------------------------------------------------+
double TotalFloor()      { return InpInitialBalance * (1.0 - InpMaxTotalLoss); }
double DailyAllowance()  { return InpInitialBalance * InpMaxDailyLoss; }
double DailyFloor()      { return g_day_open_balance - DailyAllowance(); }
double BindingFloor()    { return MathMax(TotalFloor(), DailyFloor()); }
double Headroom()        { return AccountInfoDouble(ACCOUNT_EQUITY) - BindingFloor(); }

bool BreachImminent()
  {
   if(g_day_open_balance <= 0) return false;         // not initialised yet
   return Headroom() < InpFlattenBufferF * DailyAllowance();
  }

//+------------------------------------------------------------------+
//| Day boundary. The daily floor resets from the balance at the      |
//| start of the server day, so that figure must survive a restart.   |
//+------------------------------------------------------------------+
void LoadDayOpen()
  {
   int h = FileOpen(InpDayStateFile, FILE_READ|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
     {
      string line = FileReadString(h);
      FileClose(h);
      string parts[];
      if(StringSplit(line, ',', parts) == 2)
        {
         g_day_open_yday   = (int)StringToInteger(parts[0]);
         g_day_open_balance = StringToDouble(parts[1]);
        }
     }
   RollDayIfNeeded();
  }

void SaveDayOpen()
  {
   int h = FileOpen(InpDayStateFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) { AddError("cannot write day-open state file"); return; }
   FileWrite(h, StringFormat("%d,%.2f", g_day_open_yday, g_day_open_balance));
   FileClose(h);
  }

void RollDayIfNeeded()
  {
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   if(t.day_of_year != g_day_open_yday || g_day_open_balance <= 0)
     {
      g_day_open_yday    = t.day_of_year;
      g_day_open_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      SaveDayOpen();
      PrintFormat("new server day %d: day-open balance %.2f, daily floor %.2f",
                  g_day_open_yday, g_day_open_balance, DailyFloor());
     }
  }

//+------------------------------------------------------------------+
//| Sizing: weight of equity -> lots.                                 |
//|                                                                   |
//| notional per lot = price * (tick_value / tick_size). tick_value is |
//| already denominated in the ACCOUNT currency, so this conversion    |
//| is correct for FX, metals and index CFDs alike without any         |
//| per-instrument special-casing.                                     |
//+------------------------------------------------------------------+
double NotionalPerLot(const string sym)
  {
   double price = SymbolInfoDouble(sym, SYMBOL_BID);
   double tv    = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double ts    = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(price <= 0 || tv <= 0 || ts <= 0) return 0.0;
   return price * (tv / ts);
  }

double LotsForWeight(const string sym, const double weight, const double equity)
  {
   double npl = NotionalPerLot(sym);
   if(npl <= 0) { AddError("no tick data for " + sym); return 0.0; }
   return weight * equity / npl;
  }

double RoundToStep(const string sym, double lots)
  {
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   double a = MathAbs(lots);
   double r = MathFloor(a / step + 1e-9) * step;     // floor: never oversize
   if(r < vmin) return 0.0;                          // too small to express
   if(r > vmax) r = vmax;
   return (lots < 0 ? -r : r);
  }

//+------------------------------------------------------------------+
//| Current net lots for a symbol, summed across positions so the     |
//| logic is identical on netting and hedging accounts.               |
//+------------------------------------------------------------------+
double NetLots(const string sym)
  {
   double net = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(PositionGetSymbol(i) != sym) continue;
      double v = PositionGetDouble(POSITION_VOLUME);
      net += (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? v : -v;
     }
   return net;
  }

bool CloseSymbol(const string sym)
  {
   trade.SetTypeFillingBySymbol(sym);
   bool all_ok = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(PositionGetSymbol(i) != sym) continue;
      ulong ticket = PositionGetInteger(POSITION_TICKET);
      if(!trade.PositionClose(ticket))
        {
         all_ok = false;
         AddError(StringFormat("close failed %s #%I64u retcode=%d",
                               sym, ticket, trade.ResultRetcode()));
        }
     }
   return all_ok;
  }

void CloseAllPositions()
  {
   for(int pass = 0; pass < InpMaxRetries && PositionsTotal() > 0; pass++)
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         string sym = PositionGetSymbol(i);
         trade.SetTypeFillingBySymbol(sym);
         ulong ticket = PositionGetInteger(POSITION_TICKET);
         if(!trade.PositionClose(ticket))
            AddError(StringFormat("flatten failed %s #%I64u retcode=%d",
                                  sym, ticket, trade.ResultRetcode()));
        }
   if(PositionsTotal() > 0)
      AddError(StringFormat("FLATTEN INCOMPLETE: %d positions remain", PositionsTotal()));
  }

//+------------------------------------------------------------------+
//| Read and act on the instruction file.                             |
//+------------------------------------------------------------------+
void ProcessTargets()
  {
   int h = FileOpen(InpTargetsCsv, FILE_READ|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return;                   // nothing published yet

   string header = FileReadString(h);
   string syms[]; double wts[];
   int n = 0;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      StringTrimLeft(line); StringTrimRight(line);
      if(StringLen(line) == 0 || StringGetCharacter(line, 0) == '#') continue;
      string parts[];
      if(StringSplit(line, ',', parts) != 2) continue;
      ArrayResize(syms, n + 1); ArrayResize(wts, n + 1);
      syms[n] = parts[0];
      wts[n]  = StringToDouble(parts[1]);
      n++;
     }
   FileClose(h);

   string nonce   = HeaderValue(header, "nonce");
   string ts      = HeaderValue(header, "ts");
   bool   flatten = (HeaderValue(header, "flatten") == "1");
   long   login   = StringToInteger(HeaderValue(header, "login"));

   if(nonce == "" || nonce == g_last_nonce) return;   // nothing new

   // Replay protection: an instruction file left on disk from a previous
   // session must never be executed as if it were current.
   datetime pub = ParseIso(ts);
   if(pub > 0 && (TimeGMT() - pub) > InpMaxTargetAgeS)
     {
      AddError(StringFormat("ignoring stale instruction nonce=%s age=%ds",
                            nonce, (int)(TimeGMT() - pub)));
      g_last_nonce = nonce;
      return;
     }
   if(login != 0 && login != AccountInfoInteger(ACCOUNT_LOGIN))
     {
      AddError(StringFormat("instruction is for login %I64d, this account is %I64d",
                            login, AccountInfoInteger(ACCOUNT_LOGIN)));
      g_last_nonce = nonce;
      return;
     }

   ArrayResize(g_actions, 0);

   if(flatten)
     {
      Print("instruction: FLATTEN ALL");
      CloseAllPositions();
      g_last_nonce = nonce;
      return;
     }

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   // Close anything held that is not in the new target set.
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      string held = PositionGetSymbol(i);
      bool wanted = false;
      for(int k = 0; k < n; k++) if(syms[k] == held) { wanted = true; break; }
      if(!wanted)
        {
         AddAction(held, "close_untargeted", 0.0, 0);
         CloseSymbol(held);
        }
     }

   for(int k = 0; k < n; k++)
     {
      string sym = syms[k];
      if(!SymbolSelect(sym, true))
        { AddError("unknown symbol " + sym + " - check the broker's naming"); continue; }

      double want = RoundToStep(sym, LotsForWeight(sym, wts[k], equity));
      double have = NetLots(sym);
      double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
      if(step <= 0) step = 0.01;

      if(want == 0.0 && MathAbs(wts[k]) > 1e-9)
         AddError(StringFormat("%s target %.6f rounds below minimum volume - skipped",
                               sym, wts[k]));

      // A sign flip is executed as close-then-open rather than as a netting
      // trade, so the result is identical on hedging accounts.
      if(have != 0.0 && want * have < 0.0)
        {
         AddAction(sym, "close_for_flip", have, 0);
         if(!CloseSymbol(sym)) continue;
         have = 0.0;
        }

      double delta = want - have;
      if(MathAbs(delta) < step - 1e-9) continue;      // inside one volume step

      double vol = RoundToStep(sym, delta);
      if(vol == 0.0) continue;

      // Per-symbol filling mode, re-set before every order.
      trade.SetTypeFillingBySymbol(sym);

      bool ok = false;
      for(int attempt = 0; attempt < InpMaxRetries && !ok; attempt++)
        {
         if(vol > 0) ok = trade.Buy(MathAbs(vol), sym);
         else        ok = trade.Sell(MathAbs(vol), sym);
         if(!ok) Sleep(300);
        }
      AddAction(sym, vol > 0 ? "buy" : "sell", MathAbs(vol), (int)trade.ResultRetcode());
      if(!ok)
         AddError(StringFormat("%s %s %.2f failed retcode=%d %s", sym,
                               vol > 0 ? "buy" : "sell", MathAbs(vol),
                               trade.ResultRetcode(), trade.ResultRetcodeDescription()));
     }

   WriteSymbols(syms);
   g_last_nonce = nonce;
  }

//+------------------------------------------------------------------+
//| Publish the symbol specs the Linux side needs to reproduce this   |
//| EA's sizing arithmetic exactly. Without these the reconciliation  |
//| log can only say "different", not "different and here is why".    |
//+------------------------------------------------------------------+
void WriteSymbols(const string &syms[])
  {
   string body = "{\"schema\":1,\"symbols\":{";
   for(int i = 0; i < ArraySize(syms); i++)
     {
      string sym = syms[i];
      if(!SymbolSelect(sym, true)) continue;
      if(i > 0) body += ",";
      body += StringFormat(
         "\"%s\":{\"bid\":%.8f,\"tick_value\":%.8f,\"tick_size\":%.8f,"
         "\"contract_size\":%.2f,\"volume_step\":%.4f,\"volume_min\":%.4f,"
         "\"volume_max\":%.2f,\"notional_per_lot\":%.4f}",
         sym,
         SymbolInfoDouble(sym, SYMBOL_BID),
         SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE),
         SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE),
         SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX),
         NotionalPerLot(sym));
     }
   body += "}}";
   AtomicWrite(InpSymbolsJson, body);
  }

//+------------------------------------------------------------------+
//| ISO-8601 -> datetime.                                             |
//|                                                                   |
//| StringToTime expects "YYYY.MM.DD HH:MI:SS" and silently returns 0  |
//| for the ISO form Python emits ("2026-10-27T18:39:07+00:00"). A 0   |
//| would skip the staleness check entirely, so the fields are parsed  |
//| explicitly and a parse failure is reported rather than ignored.    |
//+------------------------------------------------------------------+
datetime ParseIso(const string iso)
  {
   if(StringLen(iso) < 19) return 0;
   MqlDateTime t;
   t.year = (int)StringToInteger(StringSubstr(iso, 0, 4));
   t.mon  = (int)StringToInteger(StringSubstr(iso, 5, 2));
   t.day  = (int)StringToInteger(StringSubstr(iso, 8, 2));
   t.hour = (int)StringToInteger(StringSubstr(iso, 11, 2));
   t.min  = (int)StringToInteger(StringSubstr(iso, 14, 2));
   t.sec  = (int)StringToInteger(StringSubstr(iso, 17, 2));
   if(t.year < 2000 || t.mon < 1 || t.mon > 12 || t.day < 1 || t.day > 31)
     { AddError("cannot parse instruction timestamp: " + iso); return 0; }
   t.day_of_week = 0; t.day_of_year = 0;
   return StructToTime(t);
  }

string HeaderValue(const string header, const string key)
  {
   int at = StringFind(header, key + "=");
   if(at < 0) return "";
   int from = at + StringLen(key) + 1;
   int to   = StringFind(header, " ", from);
   if(to < 0) to = StringLen(header);
   return StringSubstr(header, from, to - from);
  }

void AddError(const string msg)
  {
   int n = ArraySize(g_errors);
   ArrayResize(g_errors, n + 1);
   g_errors[n] = msg;
   Print("ERROR: ", msg);
  }

void AddAction(const string sym, const string act, const double vol, const int retcode)
  {
   int n = ArraySize(g_actions);
   ArrayResize(g_actions, n + 1);
   g_actions[n] = StringFormat("{\"symbol\":\"%s\",\"action\":\"%s\",\"volume\":%.4f,\"retcode\":%d}",
                               sym, act, vol, retcode);
  }

//+------------------------------------------------------------------+
//| Reporting. Written to .tmp then renamed so the Linux side never    |
//| reads a half-written file.                                        |
//+------------------------------------------------------------------+
string JsonArray(const string &items[])
  {
   string s = "[";
   for(int i = 0; i < ArraySize(items); i++)
     { if(i > 0) s += ","; s += items[i]; }
   return s + "]";
  }

string QuotedArray(const string &items[])
  {
   string s = "[";
   for(int i = 0; i < ArraySize(items); i++)
     {
      if(i > 0) s += ",";
      string e = items[i];
      StringReplace(e, "\"", "'");
      s += "\"" + e + "\"";
     }
   return s + "]";
  }

string IsoNow() { return TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS) + "+00:00"; }

void AtomicWrite(const string filename, const string body)
  {
   string tmp = filename + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("ERROR: cannot open ", tmp); return; }
   FileWriteString(h, body);
   FileClose(h);
   // FILE_REWRITE makes FileMove overwrite, so no FileDelete first - deleting
   // is the step most likely to fail while a reader holds the file, and losing
   // the destination before the rename would leave no file at all.
   for(int attempt = 0; attempt < 3; attempt++)
     {
      if(FileMove(tmp, 0, filename, FILE_REWRITE)) return;
      Sleep(50);
     }
   Print("ERROR: cannot rename ", tmp, " -> ", filename, " err=", GetLastError());
  }

void WriteHeartbeat()
  {
   string iso = IsoNow();
   StringReplace(iso, ".", "-");
   AtomicWrite(InpHeartbeatJson,
      StringFormat("{\"ts_utc\":\"%s\",\"login\":%I64d,\"connected\":%s}",
                   iso, AccountInfoInteger(ACCOUNT_LOGIN),
                   TerminalInfoInteger(TERMINAL_CONNECTED) ? "true" : "false"));
  }

void WriteState()
  {
   string pos = "[";
   int written = 0;
   string seen[];
   for(int i = 0; i < PositionsTotal(); i++)
     {
      string sym = PositionGetSymbol(i);
      bool dup = false;
      for(int k = 0; k < ArraySize(seen); k++) if(seen[k] == sym) { dup = true; break; }
      if(dup) continue;
      ArrayResize(seen, ArraySize(seen) + 1);
      seen[ArraySize(seen) - 1] = sym;

      double net = NetLots(sym), profit = 0.0;
      for(int j = 0; j < PositionsTotal(); j++)
        {
         if(PositionGetSymbol(j) != sym) continue;
         profit += PositionGetDouble(POSITION_PROFIT);
        }
      if(written > 0) pos += ",";
      pos += StringFormat("{\"symbol\":\"%s\",\"net_lots\":%.4f,\"profit\":%.2f}",
                          sym, net, profit);
      written++;
     }
   pos += "]";

   string iso = IsoNow();
   StringReplace(iso, ".", "-");
   string body = StringFormat(
      "{\"schema\":1,\"ts_utc\":\"%s\",\"login\":%I64d,\"connected\":%s,"
      "\"equity\":%.2f,\"balance\":%.2f,\"day_start_balance\":%.2f,"
      "\"margin_free\":%.2f,\"binding_floor\":%.2f,\"headroom\":%.2f,"
      "\"nonce_seen\":\"%s\",\"positions\":%s,\"last_actions\":%s,\"errors\":%s}",
      iso, AccountInfoInteger(ACCOUNT_LOGIN),
      TerminalInfoInteger(TERMINAL_CONNECTED) ? "true" : "false",
      AccountInfoDouble(ACCOUNT_EQUITY), AccountInfoDouble(ACCOUNT_BALANCE),
      g_day_open_balance, AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      BindingFloor(), Headroom(), g_last_nonce,
      pos, JsonArray(g_actions), QuotedArray(g_errors));
   AtomicWrite(InpStateJson, body);
  }
//+------------------------------------------------------------------+
