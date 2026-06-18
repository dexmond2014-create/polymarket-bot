//+------------------------------------------------------------------+
//|                                              AlligatorGoldBot.mq5 |
//|        Alligator Gold Bot — Multi-Timeframe with News Filter      |
//|                              (MetaTrader 5 / MQL5)                |
//+------------------------------------------------------------------+
#property copyright "Claude AI"
#property link      "https://www.mql5.com"
#property version   "3.14"

#include <Trade\Trade.mqh>

//--- Inputs
input double LotSize         = 0.01;   // Lot size per trade
input int    StopLoss        = 8000;   // Stop loss, in points (gold: 30, crypto: 8000)
input int    BreakevenProfit = 8000;   // Move SL to breakeven after this many points of profit (~$80 on 0.01 lot, matches StopLoss risk)
input int    RetraceProfit   = 2000;   // Close trade if profit retraces this many points from its peak (~$20 on 0.01 lot)
input bool   UseNewsFilter   = false;  // Pause new entries during news hours (off — StopLoss already caps risk)
input int    MagicNumber     = 123456;
input int    MaxTrades       = 2;      // Max simultaneous open positions for this EA

input ENUM_TIMEFRAMES EntryTimeframe = PERIOD_M15; // Entry timeframe (M5 for faster entries, M15 for standard)
input bool   RequireTwoCandles = false;  // Require 2 consecutive bullish/bearish closed candles before entry

input bool   RequireH4 = true;   // Require H4 Alligator trend confirmation
input bool   RequireH1 = false;  // Require 1H Alligator trend confirmation
input bool   RequireW1 = false;  // Require Weekly Alligator trend confirmation (very strict, often "sleeping")

input bool   AllowPullbackEntry = true; // Also enter when price retraces back to the Lips/Teeth zone during a fanned Alligator
input int    CooldownMinutes = 15; // Wait this long after a trade closes before re-checking for entries

CTrade trade;

int hAlligatorEntry = INVALID_HANDLE;
int hAlligatorH1    = INVALID_HANDLE;
int hAlligatorH4    = INVALID_HANDLE;
int hAlligatorW1    = INVALID_HANDLE;

struct PositionState
{
   ulong  ticket;
   double peakProfitPoints;
   bool   breakeven;
};
PositionState g_positions[];

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   ConfigureFillingMode();

   hAlligatorEntry = iAlligator(_Symbol, EntryTimeframe, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorH1    = iAlligator(_Symbol, PERIOD_H1, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorH4    = iAlligator(_Symbol, PERIOD_H4, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorW1    = iAlligator(_Symbol, PERIOD_W1, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);

   if(hAlligatorEntry == INVALID_HANDLE || hAlligatorH1 == INVALID_HANDLE ||
      hAlligatorH4 == INVALID_HANDLE || hAlligatorW1 == INVALID_HANDLE)
   {
      Print("Failed to create one or more Alligator indicator handles");
      return INIT_FAILED;
   }

   string tfName = EnumToString(EntryTimeframe);
   Print("=== Alligator Gold Bot Started ===");
   Print("Lot Size: ",      LotSize);
   Print("News Filter: ",   UseNewsFilter);
   Print("Max Trades: ",    MaxTrades);
   Print("Entry TF: ",      tfName);
   Print("Two Candles: ",   RequireTwoCandles);

   RebuildPositionTracking();

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(hAlligatorEntry != INVALID_HANDLE) IndicatorRelease(hAlligatorEntry);
   if(hAlligatorH1    != INVALID_HANDLE) IndicatorRelease(hAlligatorH1);
   if(hAlligatorH4    != INVALID_HANDLE) IndicatorRelease(hAlligatorH4);
   if(hAlligatorW1    != INVALID_HANDLE) IndicatorRelease(hAlligatorW1);
   Print("=== Bot Stopped ===");
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
datetime g_lastStatusTime    = 0;
datetime g_lastTradeCloseTime = 0;
bool     g_positionsSynced   = false;

void OnTick()
{
   // OnInit() can run before the terminal finishes syncing open positions
   // from the broker right after a cold start, so retry once here — by
   // the first tick the connection is guaranteed to be fully synced.
   if(!g_positionsSynced)
   {
      RebuildPositionTracking();
      g_positionsSynced = true;
   }

   ManageOpenPositions();

   // Print a status snapshot every 5 minutes of wall-clock time (for debugging)
   if(TimeCurrent() - g_lastStatusTime >= 300)
   {
      g_lastStatusTime = TimeCurrent();
      PrintAlligatorStatus();
   }

   bool cooldownActive = (TimeCurrent() - g_lastTradeCloseTime) < CooldownMinutes * 60;

   if(!cooldownActive && CountMyPositions() < MaxTrades)
      CheckAlligatorSetup();
}

//+------------------------------------------------------------------+
//| Debug: print Alligator readings + trend flags for all timeframes  |
//+------------------------------------------------------------------+
void PrintAlligatorStatus()
{
   double jawE, teethE, lipsE;
   bool okEntry = GetAlligator(hAlligatorEntry, jawE, teethE, lipsE);

   bool upH1 = IsUptrendAlligator(hAlligatorH1);
   bool upH4 = IsUptrendAlligator(hAlligatorH4);
   bool upW1 = IsUptrendAlligator(hAlligatorW1);

   bool dnH1 = IsDowntrendAlligator(hAlligatorH1);
   bool dnH4 = IsDowntrendAlligator(hAlligatorH4);
   bool dnW1 = IsDowntrendAlligator(hAlligatorW1);

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   string tfName = EnumToString(EntryTimeframe);

   MqlDateTime dtNow;
   TimeToStruct(TimeCurrent(), dtNow);
   PrintFormat("[status] News filter active=%s (server hour=%d)",
               IsMajorNewsTime() ? "true" : "false", dtNow.hour);

   if(!okEntry)
   {
      PrintFormat("[status] %s Alligator data not ready yet (not enough history)", tfName);
   }
   else
   {
      PrintFormat("[status] %s jaw=%.5f teeth=%.5f lips=%.5f bid=%.5f bid>lips=%s",
                  tfName, jawE, teethE, lipsE, bid, (bid > lipsE ? "true" : "false"));
   }

   PrintFormat("[status] H1 up=%s down=%s | H4 up=%s down=%s | W1 up=%s down=%s",
               upH1 ? "true" : "false", dnH1 ? "true" : "false",
               upH4 ? "true" : "false", dnH4 ? "true" : "false",
               upW1 ? "true" : "false", dnW1 ? "true" : "false");

   if(!upH1 && !dnH1) Print("[status] H1 Alligator: no data / no clear trend (lines not fanned)");
   if(!upH4 && !dnH4) Print("[status] H4 Alligator: no data / no clear trend (lines not fanned)");
   if(!upW1 && !dnW1) Print("[status] W1 Alligator: no data / no clear trend (lines not fanned)");

   if(RequireTwoCandles)
   {
      double c1 = iClose(_Symbol, EntryTimeframe, 1);
      double o1 = iOpen(_Symbol,  EntryTimeframe, 1);
      double c2 = iClose(_Symbol, EntryTimeframe, 2);
      double o2 = iOpen(_Symbol,  EntryTimeframe, 2);
      bool twoGreen = (c1 > o1 && c2 > o2);
      bool twoRed   = (c1 < o1 && c2 < o2);
      PrintFormat("[status] Last 2 candles: green=%s red=%s",
                  twoGreen ? "true" : "false", twoRed ? "true" : "false");
   }
}

//+------------------------------------------------------------------+
//| Pick an order filling mode the broker actually supports           |
//+------------------------------------------------------------------+
void ConfigureFillingMode()
{
   long filling = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      trade.SetTypeFilling(ORDER_FILLING_RETURN);
}

//+------------------------------------------------------------------+
//| Static news-hour pause window (placeholder, broker/server time)   |
//+------------------------------------------------------------------+
bool IsMajorNewsTime()
{
   if(!UseNewsFilter)
      return false;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   if(dt.hour >= 12 && dt.hour <= 16)
      return true;

   if(dt.hour >= 20 && dt.hour <= 22)
      return true;

   return false;
}

//+------------------------------------------------------------------+
//| Read Jaw/Teeth/Lips from an Alligator handle (last closed bar)    |
//+------------------------------------------------------------------+
bool GetAlligator(int handle, double &jaw, double &teeth, double &lips, int shift = 1)
{
   double jawBuf[1], teethBuf[1], lipsBuf[1];

   if(CopyBuffer(handle, GATORJAW_LINE,   shift, 1, jawBuf)   <= 0) return false;
   if(CopyBuffer(handle, GATORTEETH_LINE, shift, 1, teethBuf) <= 0) return false;
   if(CopyBuffer(handle, GATORLIPS_LINE,  shift, 1, lipsBuf)  <= 0) return false;

   jaw   = jawBuf[0];
   teeth = teethBuf[0];
   lips  = lipsBuf[0];
   return true;
}

//+------------------------------------------------------------------+
bool IsUptrendAlligator(int handle)
{
   double jaw, teeth, lips;
   if(!GetAlligator(handle, jaw, teeth, lips))
      return false;
   return (lips > teeth && teeth > jaw);
}

//+------------------------------------------------------------------+
bool IsDowntrendAlligator(int handle)
{
   double jaw, teeth, lips;
   if(!GetAlligator(handle, jaw, teeth, lips))
      return false;
   return (jaw > teeth && teeth > lips);
}

//+------------------------------------------------------------------+
//| Check 2 consecutive closed candles are bullish or bearish         |
//+------------------------------------------------------------------+
bool TwoConsecutiveBullish()
{
   double c1 = iClose(_Symbol, EntryTimeframe, 1);
   double o1 = iOpen(_Symbol,  EntryTimeframe, 1);
   double c2 = iClose(_Symbol, EntryTimeframe, 2);
   double o2 = iOpen(_Symbol,  EntryTimeframe, 2);
   return (c1 > o1 && c2 > o2);
}

bool TwoConsecutiveBearish()
{
   double c1 = iClose(_Symbol, EntryTimeframe, 1);
   double o1 = iOpen(_Symbol,  EntryTimeframe, 1);
   double c2 = iClose(_Symbol, EntryTimeframe, 2);
   double o2 = iOpen(_Symbol,  EntryTimeframe, 2);
   return (c1 < o1 && c2 < o2);
}

//+------------------------------------------------------------------+
//| Multi-timeframe Alligator entry check                              |
//+------------------------------------------------------------------+
void CheckAlligatorSetup()
{
   if(IsMajorNewsTime())
      return;

   double jawE, teethE, lipsE;
   if(!GetAlligator(hAlligatorEntry, jawE, teethE, lipsE))
      return;

   // If a higher-timeframe filter is disabled, treat it as automatically passed
   bool uptrendH1  = !RequireH1 || IsUptrendAlligator(hAlligatorH1);
   bool uptrendH4  = !RequireH4 || IsUptrendAlligator(hAlligatorH4);
   bool uptrendW1  = !RequireW1 || IsUptrendAlligator(hAlligatorW1);

   bool downtrendH1 = !RequireH1 || IsDowntrendAlligator(hAlligatorH1);
   bool downtrendH4 = !RequireH4 || IsDowntrendAlligator(hAlligatorH4);
   bool downtrendW1 = !RequireW1 || IsDowntrendAlligator(hAlligatorW1);

   // Two-candle confirmation
   bool bullishCandles = !RequireTwoCandles || TwoConsecutiveBullish();
   bool bearishCandles = !RequireTwoCandles || TwoConsecutiveBearish();

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // Normal entry: price above/below Lips
   bool entryBuy  = (bid > lipsE);
   bool entrySell = (ask < lipsE);

   // Pullback entry: price retraces into Teeth-Lips zone while Alligator stays fanned
   if(AllowPullbackEntry)
   {
      bool alligatorBullish = (lipsE > teethE && teethE > jawE);
      bool alligatorBearish = (jawE  > teethE && teethE > lipsE);

      if(alligatorBullish && bid >= teethE && bid <= lipsE)
         entryBuy = true;

      if(alligatorBearish && ask <= teethE && ask >= lipsE)
         entrySell = true;
   }

   if(entryBuy && uptrendH1 && uptrendH4 && uptrendW1 && bullishCandles)
   {
      string reason = (bid > lipsE) ? "breakout" : "pullback-to-lips";
      PrintFormat("BUY signal (%s, %s entry, 2-candle=%s)",
                  EnumToString(EntryTimeframe), reason, RequireTwoCandles ? "true" : "off");
      OpenBuy();
      return;
   }

   if(entrySell && downtrendH1 && downtrendH4 && downtrendW1 && bearishCandles)
   {
      string reason = (ask < lipsE) ? "breakdown" : "pullback-to-lips";
      PrintFormat("SELL signal (%s, %s entry, 2-candle=%s)",
                  EnumToString(EntryTimeframe), reason, RequireTwoCandles ? "true" : "off");
      OpenSell();
   }
}

//+------------------------------------------------------------------+
void OpenBuy()
{
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point  = _Point;
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double sl     = NormalizeDouble(ask - StopLoss * point, digits);

   if(trade.Buy(LotSize, _Symbol, ask, sl, 0, "Alligator BUY"))
   {
      RegisterPosition(trade.ResultOrder());
      Print("BUY opened at ", ask);
   }
   else
   {
      Print("BUY failed: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
void OpenSell()
{
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point  = _Point;
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl     = NormalizeDouble(bid + StopLoss * point, digits);

   if(trade.Sell(LotSize, _Symbol, bid, sl, 0, "Alligator SELL"))
   {
      RegisterPosition(trade.ResultOrder());
      Print("SELL opened at ", bid);
   }
   else
   {
      Print("SELL failed: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Re-attach to any of this EA's positions already open on restart   |
//| (recompile / input change / terminal restart), so breakeven and   |
//| trailing keep working instead of forgetting about live trades.    |
//+------------------------------------------------------------------+
void RebuildPositionTracking()
{
   double point = _Point;

   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      bool alreadyTracked = false;
      for(int j = 0; j < ArraySize(g_positions); j++)
      {
         if(g_positions[j].ticket == ticket)
         {
            alreadyTracked = true;
            break;
         }
      }
      if(alreadyTracked)
         continue;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double profitPoints = (type == POSITION_TYPE_BUY) ?
                             (bid - openPrice) / point : (openPrice - ask) / point;

      int size = ArraySize(g_positions);
      ArrayResize(g_positions, size + 1);
      g_positions[size].ticket           = ticket;
      g_positions[size].peakProfitPoints = MathMax(profitPoints, 0);
      g_positions[size].breakeven        = false;

      PrintFormat("Re-attached to existing position #%I64u (profit=%.1f pts)", ticket, profitPoints);
   }
}

//+------------------------------------------------------------------+
//| Track a newly opened position for breakeven/trailing management   |
//+------------------------------------------------------------------+
void RegisterPosition(ulong ticket)
{
   int size = ArraySize(g_positions);
   ArrayResize(g_positions, size + 1);
   g_positions[size].ticket           = ticket;
   g_positions[size].peakProfitPoints = 0;
   g_positions[size].breakeven        = false;
}

//+------------------------------------------------------------------+
//| Count this EA's open positions on this symbol                     |
//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Breakeven + retrace-from-peak trailing exit                       |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   double point = _Point;

   for(int i = ArraySize(g_positions) - 1; i >= 0; i--)
   {
      ulong ticket = g_positions[i].ticket;

      if(!PositionSelectByTicket(ticket))
      {
         // Position already closed (e.g. stop loss hit) — stop tracking it
         g_lastTradeCloseTime = TimeCurrent();
         ArrayRemove(g_positions, i, 1);
         continue;
      }

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double tp        = PositionGetDouble(POSITION_TP);
      double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double profitPoints;
      if(type == POSITION_TYPE_BUY)
         profitPoints = (bid - openPrice) / point;
      else
         profitPoints = (openPrice - ask) / point;

      if(profitPoints > g_positions[i].peakProfitPoints)
         g_positions[i].peakProfitPoints = profitPoints;

      // Move stop loss to breakeven once profit target is reached
      if(profitPoints >= BreakevenProfit && !g_positions[i].breakeven)
      {
         int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
         double newSL  = NormalizeDouble(openPrice, digits);
         if(trade.PositionModify(ticket, newSL, tp))
            g_positions[i].breakeven = true;
      }

      // Close if profit retraces too far from its peak after breakeven
      if(g_positions[i].breakeven &&
         profitPoints < (g_positions[i].peakProfitPoints - RetraceProfit))
      {
         if(trade.PositionClose(ticket))
            PrintFormat("[exit] Closed ticket %I64u | peak profit %.1f pts, retraced by %.1f pts",
                        ticket, g_positions[i].peakProfitPoints,
                        (g_positions[i].peakProfitPoints - profitPoints));
         g_lastTradeCloseTime = TimeCurrent();
         ArrayRemove(g_positions, i, 1);
      }
   }
}
