//+------------------------------------------------------------------+
//|  DumpSymbols.mq5   -  run ONCE as a Script, not an EA            |
//|                                                                  |
//|  Writes every instrument this account can actually trade, with   |
//|  the specs needed for position sizing, to MQL5\Files.            |
//|                                                                  |
//|  This answers the question that has blocked the whole plan: what  |
//|  does FTMO US via OANDA actually offer, and in particular does it |
//|  offer any BOND or RATE instrument? The bond/credit sleeve was    |
//|  the best in the entire system at Sharpe 0.87, and every          |
//|  published instrument list says FTMO has none. Confirm it from    |
//|  the account rather than from a review site.                      |
//|                                                                  |
//|  Usage: drag onto any chart, allow it to run, then read           |
//|         MQL5\Files\tcs_all_symbols.json                           |
//+------------------------------------------------------------------+
#property copyright "tcs"
#property version   "1.00"
// NOTE: script_show_inputs is deliberately absent. It opens a modal
// parameter dialog at launch, which blocks forever on a headless VM.
// Inputs keep their declared defaults.

input string InpOutFile      = "tcs_all_symbols.json";
input bool   InpMarketWatchOnly = false;  // false = every symbol the broker lists

//+------------------------------------------------------------------+
//| Escape a value for embedding in JSON.                             |
//| SYMBOL_PATH is a backslash-separated tree ("Forex\\Majors"), and an  |
//| unescaped backslash makes the entire document unparseable.        |
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
   int total = InpMarketWatchOnly ? SymbolsTotal(true) : SymbolsTotal(false);
   PrintFormat("dumping %d symbols (market watch only = %s)",
               total, InpMarketWatchOnly ? "true" : "false");

   string body = StringFormat(
      "{\"schema\":1,\"login\":%I64d,\"server\":\"%s\",\"company\":\"%s\","
      "\"currency\":\"%s\",\"balance\":%.2f,\"equity\":%.2f,"
      "\"margin_mode\":%d,\"leverage\":%I64d,\"count\":%d,\"symbols\":[",
      AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoString(ACCOUNT_SERVER),
      AccountInfoString(ACCOUNT_COMPANY),
      AccountInfoString(ACCOUNT_CURRENCY),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      (int)AccountInfoInteger(ACCOUNT_MARGIN_MODE),
      AccountInfoInteger(ACCOUNT_LEVERAGE),
      total);

   int written = 0;
   for(int i = 0; i < total; i++)
     {
      string sym = SymbolName(i, InpMarketWatchOnly);
      if(sym == "") continue;
      // Selecting into Market Watch is what makes tick data available; without
      // it bid/tick_value read as zero and the spec is useless for sizing.
      if(!SymbolSelect(sym, true)) continue;

      double bid  = SymbolInfoDouble(sym, SYMBOL_BID);
      double tv   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
      double ts   = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      double npl  = (bid > 0 && tv > 0 && ts > 0) ? bid * (tv / ts) : 0.0;
      long   spread = SymbolInfoInteger(sym, SYMBOL_SPREAD);
      double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
      double spread_bps = (bid > 0 && point > 0) ? (spread * point / bid) * 10000.0 : 0.0;

      if(written > 0) body += ",";
      body += StringFormat(
         "{\"symbol\":\"%s\",\"path\":\"%s\",\"description\":\"%s\","
         "\"bid\":%.8f,\"tick_value\":%.8f,\"tick_size\":%.8f,"
         "\"contract_size\":%.2f,\"notional_per_lot\":%.4f,"
         "\"volume_min\":%.4f,\"volume_step\":%.4f,\"volume_max\":%.2f,"
         "\"spread_points\":%I64d,\"spread_bps\":%.3f,"
         "\"trade_mode\":%d,\"swap_long\":%.4f,\"swap_short\":%.4f,"
         "\"margin_currency\":\"%s\"}",
         sym,
         JsonEsc(SymbolInfoString(sym, SYMBOL_PATH)),
         JsonEsc(SymbolInfoString(sym, SYMBOL_DESCRIPTION)),
         bid, tv, ts,
         SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), npl,
         SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP),
         SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX),
         spread, spread_bps,
         (int)SymbolInfoInteger(sym, SYMBOL_TRADE_MODE),
         SymbolInfoDouble(sym, SYMBOL_SWAP_LONG),
         SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT),
         SymbolInfoString(sym, SYMBOL_CURRENCY_MARGIN));
      written++;
     }
   body += "]}";

   int h = FileOpen(InpOutFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE)
     { PrintFormat("ERROR: cannot write %s (err %d)", InpOutFile, GetLastError()); return; }
   FileWriteString(h, body);
   FileClose(h);
   PrintFormat("wrote %d symbols to MQL5\\Files\\%s", written, InpOutFile);
  }
