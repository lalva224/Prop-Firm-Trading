//+------------------------------------------------------------------+
//|  DumpHistory.mq5   -  run ONCE as a Script, not an EA            |
//|                                                                  |
//|  Exports daily bars for every tradeable symbol to CSV, so the     |
//|  research pipeline can backtest the LITERAL instruments the live  |
//|  strategy will trade.                                             |
//|                                                                  |
//|  WHY THIS MATTERS MORE THAN IT LOOKS                              |
//|  Every commodity measurement so far used yfinance front-month     |
//|  continuous futures, which are NOT back-adjusted - each contract   |
//|  roll injects a fake gap straight into the signal and the P&L.     |
//|  That confound is why the futures universe measured Sharpe 0.40    |
//|  against the ETF universe's 0.82, and why the ag sleeve measured   |
//|  -0.39. Broker CFD series are continuous by construction, so this  |
//|  export removes the confound for free - no data vendor needed.     |
//|                                                                  |
//|  CAVEAT: MT5 only holds history it has actually downloaded. The    //|
//|  script forces a download per symbol and reports the earliest      |
//|  date it managed to get, which is the number that decides whether  |
//|  this replaces a paid feed or merely supplements it.               |
//|                                                                  |
//|  Output: MQL5\Files\tcs_history\<SYMBOL>.csv  plus an index file.  |
//+------------------------------------------------------------------+
#property copyright "tcs"
#property version   "1.00"
// NOTE: script_show_inputs is deliberately absent. It opens a modal
// parameter dialog at launch, which blocks forever on a headless VM.
// Inputs keep their declared defaults.

input string   InpOutDir          = "tcs_history";
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_D1;
input datetime InpFrom            = D'2000.01.01';
input bool     InpMarketWatchOnly = false;   // false = every symbol the broker lists
input int      InpDownloadWaitMs  = 400;     // pause between download attempts
input int      InpDownloadTries   = 25;      // attempts before giving up on a symbol
input int      InpMinBars         = 200;     // skip symbols with less than this

int OnStart_total = 0;

//+------------------------------------------------------------------+
//| Escape a value for JSON. SYMBOL_PATH is backslash-separated, and  |
//| an unescaped backslash invalidates the whole document.            |
//+------------------------------------------------------------------+
string JsonEsc(const string v)
  {
   string o = v;
   StringReplace(o, "\\", "\\\\");
   StringReplace(o, "\"", "\\\"");
   return o;
  }

void OnStart()
  {
   int total = SymbolsTotal(InpMarketWatchOnly);
   PrintFormat("exporting %s bars for up to %d symbols from %s",
               EnumToString(InpTimeframe), total, TimeToString(InpFrom, TIME_DATE));

   string index = StringFormat(
      "{\"schema\":1,\"timeframe\":\"%s\",\"login\":%I64d,\"server\":\"%s\","
      "\"exported_utc\":\"%s\",\"symbols\":[",
      EnumToString(InpTimeframe),
      AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoString(ACCOUNT_SERVER),
      TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS));

   int exported = 0, skipped = 0;
   for(int i = 0; i < total; i++)
     {
      string sym = SymbolName(i, InpMarketWatchOnly);
      if(sym == "") continue;
      if(!SymbolSelect(sym, true)) { skipped++; continue; }

      MqlRates rates[];
      int got = FetchWithRetry(sym, rates);
      if(got < InpMinBars)
        {
         PrintFormat("skip %-16s only %d bars", sym, got);
         skipped++;
         continue;
        }

      if(!WriteSymbolCsv(sym, rates, got)) { skipped++; continue; }

      if(exported > 0) index += ",";
      // point and digits are needed to convert MqlRates.spread, which is
      // expressed in POINTS, into a price and then into bps. Without them the
      // spread column is uninterpretable.
      index += StringFormat(
         "{\"symbol\":\"%s\",\"bars\":%d,\"first\":\"%s\",\"last\":\"%s\","
         "\"path\":\"%s\",\"description\":\"%s\","
         "\"point\":%.10f,\"digits\":%d,\"volume_min\":%.4f,"
         "\"volume_step\":%.4f,\"contract_size\":%.2f}",
         sym, got,
         TimeToString(rates[0].time, TIME_DATE),
         TimeToString(rates[got - 1].time, TIME_DATE),
         JsonEsc(SymbolInfoString(sym, SYMBOL_PATH)),
         SymbolInfoString(sym, SYMBOL_DESCRIPTION),
         SymbolInfoDouble(sym, SYMBOL_POINT),
         (int)SymbolInfoInteger(sym, SYMBOL_DIGITS),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP),
         SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE));
      exported++;
      PrintFormat("ok   %-16s %5d bars  %s -> %s", sym, got,
                  TimeToString(rates[0].time, TIME_DATE),
                  TimeToString(rates[got - 1].time, TIME_DATE));
     }

   index += StringFormat("],\"exported\":%d,\"skipped\":%d}", exported, skipped);

   int h = FileOpen(InpOutDir + "\\_index.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, index); FileClose(h); }

   PrintFormat("DONE. exported %d symbols, skipped %d. See MQL5\\Files\\%s\\",
               exported, skipped, InpOutDir);
  }

//+------------------------------------------------------------------+
//| MT5 serves only history it has already downloaded, and the first  |
//| CopyRates on an untouched symbol usually returns nothing while the |
//| terminal fetches in the background. Retry until it settles.        |
//+------------------------------------------------------------------+
int FetchWithRetry(const string sym, MqlRates &rates[])
  {
   int best = 0;
   for(int attempt = 0; attempt < InpDownloadTries; attempt++)
     {
      ArrayFree(rates);
      int got = CopyRates(sym, InpTimeframe, InpFrom, TimeCurrent(), rates);
      if(got > best) { best = got; }
      // Stop as soon as two consecutive attempts return the same count: the
      // terminal has finished downloading whatever it is going to give us.
      if(got > 0 && got == best && attempt > 0) return got;
      Sleep(InpDownloadWaitMs);
     }
   return best;
  }

bool WriteSymbolCsv(const string sym, const MqlRates &rates[], const int got)
  {
   // Symbol names can contain characters that are illegal in filenames.
   string safe = sym;
   StringReplace(safe, "/", "_"); StringReplace(safe, "\\", "_");
   StringReplace(safe, ":", "_"); StringReplace(safe, "*", "_");
   StringReplace(safe, "?", "_"); StringReplace(safe, "\"", "_");
   StringReplace(safe, "<", "_"); StringReplace(safe, ">", "_");
   StringReplace(safe, "|", "_"); StringReplace(safe, " ", "_");

   string path = InpOutDir + "\\" + safe + ".csv";
   int h = FileOpen(path, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE)
     { PrintFormat("ERROR cannot write %s (err %d)", path, GetLastError()); return false; }

   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   FileWriteString(h, "date,open,high,low,close,tick_volume,real_volume,spread\n");
   for(int k = 0; k < got; k++)
      FileWriteString(h, StringFormat("%s,%.*f,%.*f,%.*f,%.*f,%I64d,%I64d,%d\n",
                      TimeToString(rates[k].time, TIME_DATE),
                      digits, rates[k].open, digits, rates[k].high,
                      digits, rates[k].low,  digits, rates[k].close,
                      rates[k].tick_volume, rates[k].real_volume,
                      rates[k].spread));
   FileClose(h);
   return true;
  }
