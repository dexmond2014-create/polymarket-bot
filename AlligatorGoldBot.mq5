//+------------------------------------------------------------------+
//|                                              AlligatorGoldBot.mq5 |
//|        Alligator Gold Bot — Multi-Timeframe with News Filter      |
//|                              (MetaTrader 5 / MQL5)                |
//+------------------------------------------------------------------+
#property copyright "Claude AI"
#property link      "https://www.mql5.com"
#property version   "3.00"

#include <Trade\Trade.mqh>

//--- Inputs
input double LotSize         = 0.01;   // Lot size per trade
input int    StopLoss        = 8000;   // Stop loss, in points (gold: 30, crypto: 8000)
input int    BreakevenProfit = 15000;  // Move SL to breakeven after this many points of profit (gold: 20, crypto: 15000)
input int    RetraceProfit   = 5000;   // Close trade if profit retraces this many points from its peak (gold: 20, crypto: 5000)
input bool   UseNewsFilter   = true;   // Pause new entries during news hours
input int    MagicNumber     = 123456;
input int    MaxTrades       = 2;      // Max simultaneous open positions for this EA

input bool   RequireH4 = true;   // Require H4 Alligator trend confirmation
input bool   RequireH1 = true;   // Require 1H Alligator trend confirmation
input bool   RequireW1 = false;  // Require Weekly Alligator trend confirmation (very strict, often "sleeping")

input bool   RequireMA200 = true;  // Require 15M Alligator (jaw/teeth/lips) all above/below the 200 MA
input int    MA200Period  = 200;   // Period of the 15M moving average used for trend confirmation

input int    CooldownMinutes = 15; // Wait this long after a trade closes before re-checking for entries

CTrade trade;

int hAlligator15M = INVALID_HANDLE;
int hAlligatorH1 = INVALID_HANDLE;
int hAlligatorH4 = INVALID_HANDLE;
int hAlligatorW1 = INVALID_HANDLE;
int hMA200_15M = INVALID_HANDLE;

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

   hAlligator15M = iAlligator(_Symbol, PERIOD_M15, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorH1 = iAlligator(_Symbol, PERIOD_H1, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorH4 = iAlligator(_Symbol, PERIOD_H4, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorW1 = iAlligator(_Symbol, PERIOD_W1, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hMA200_15M = iMA(_Symbol, PERIOD_M15, MA200Period, 0, MODE_SMA, PRICE_CLOSE);

   if(hAlligator15M == INVALID_HANDLE || hAlligatorH1 == INVALID_HANDLE ||
      hAlligatorH4 == INVALID_HANDLE || hAlligatorW1 == INVALID_HANDLE ||
      hMA200_15M == INVALID_HANDLE)
   {
      Print("Failed to create one or more Alligator/MA indicator handles");
      return INIT_FAILED;
   }

   Print("=== Alligator Gold Bot Started ===");
   Print("Lot Size: ",    LotSize);
   Print("News Filter: ", UseNewsFilter);
   Print("Max Trades: ",  MaxTrades);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(hAlligator15M != INVALID_HANDLE) IndicatorRelease(hAlligator15M);
   if(hAlligatorH1 != INVALID_HANDLE) IndicatorRelease(hAlligatorH1);
   if(hAlligatorH4 != INVALID_HANDLE) IndicatorRelease(hAlligatorH4);
   if(hAlligatorW1 != INVALID_HANDLE) IndicatorRelease(hAlligatorW1);
   if(hMA200_15M != INVALID_HANDLE) IndicatorRelease(hMA200_15M);
   Print("=== Bot Stopped ===");
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
datetime g_lastStatusTime = 0;
datetime g_lastTradeCloseTime = 0;

void OnTick()
{
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
   double jaw15M, teeth15M, lips15M;
   bool ok15M = GetAlligator(hAlligator15M, jaw15M, teeth15M, lips15M);

   bool upH1 = IsUptrendAlligator(hAlligatorH1);
   bool upH4 = IsUptrendAlligator(hAlligatorH4);
   bool upW1 = IsUptrendAlligator(hAlligatorW1);

   bool dnH1 = IsDowntrendAlligator(hAlligatorH1);
   bool dnH4 = IsDowntrendAlligator(hAlligatorH4);
   bool dnW1 = IsDowntrendAlligator(hAlligatorW1);

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(!ok15M)
   {
      Print("[status] 15M Alligator data not ready yet (not enough history)");
   }
   else
   {
      PrintFormat("[status] 15M jaw=%.5f teeth=%.5f lips=%.5f bid=%.5f bid>lips=%s",
                  jaw15M, teeth15M, lips15M, bid, (bid > lips15M ? "true" : "false"));
   }

   PrintFormat("[status] H1 up=%s down=%s | H4 up=%s down=%s | W1 up=%s down=%s",
               upH1 ? "true" : "false", dnH1 ? "true" : "false",
               upH4 ? "true" : "false", dnH4 ? "true" : "false",
               upW1 ? "true" : "false", dnW1 ? "true" : "false");

   if(!upH1 && !dnH1) Print("[status] H1 Alligator: no data / no clear trend (lines not fanned)");
   if(!upH4 && !dnH4) Print("[status] H4 Alligator: no data / no clear trend (lines not fanned)");
   if(!upW1 && !dnW1) Print("[status] W1 Alligator: no data / no clear trend (lines not fanned)");

   if(RequireMA200)
   {
      double ma200;
      if(ok15M && GetMA(hMA200_15M, ma200))
      {
         bool above = (jaw15M > ma200 && teeth15M > ma200 && lips15M > ma200);
         bool below = (jaw15M < ma200 && teeth15M < ma200 && lips15M < ma200);
         PrintFormat("[status] MA200(15M)=%.5f | Alligator above=%s below=%s",
                     ma200, above ? "true" : "false", below ? "true" : "false");
      }
      else
      {
         Print("[status] MA200(15M) data not ready yet (not enough history)");
      }
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
//| Read a moving average value (last closed bar)                     |
//+------------------------------------------------------------------+
bool GetMA(int handle, double &value, int shift = 1)
{
   double buf[1];
   if(CopyBuffer(handle, 0, shift, 1, buf) <= 0) return false;
   value = buf[0];
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
//| Multi-timeframe Alligator entry check                              |
//+------------------------------------------------------------------+
void CheckAlligatorSetup()
{
   if(IsMajorNewsTime())
      return;

   double jaw15M, teeth15M, lips15M;
   if(!GetAlligator(hAlligator15M, jaw15M, teeth15M, lips15M))
      return;

   // If a higher-timeframe filter is disabled, treat it as automatically passed
   bool uptrendH1  = !RequireH1 || IsUptrendAlligator(hAlligatorH1);
   bool uptrendH4  = !RequireH4 || IsUptrendAlligator(hAlligatorH4);
   bool uptrendW1  = !RequireW1 || IsUptrendAlligator(hAlligatorW1);

   bool downtrendH1 = !RequireH1 || IsDowntrendAlligator(hAlligatorH1);
   bool downtrendH4 = !RequireH4 || IsDowntrendAlligator(hAlligatorH4);
   bool downtrendW1 = !RequireW1 || IsDowntrendAlligator(hAlligatorW1);

   // 200 MA filter: all 3 Alligator lines must sit above/below the 15M MA200
   bool aboveMA200 = true;
   bool belowMA200 = true;
   if(RequireMA200)
   {
      double ma200;
      if(!GetMA(hMA200_15M, ma200))
         return;

      aboveMA200 = (jaw15M > ma200 && teeth15M > ma200 && lips15M > ma200);
      belowMA200 = (jaw15M < ma200 && teeth15M < ma200 && lips15M < ma200);
   }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(bid > lips15M && uptrendH1 && uptrendH4 && uptrendW1 && aboveMA200)
   {
      Print("BUY signal (15M entry, higher-TF confirmed, MA200 aligned)");
      OpenBuy();
      return;
   }

   if(ask < lips15M && downtrendH1 && downtrendH4 && downtrendW1 && belowMA200)
   {
      Print("SELL signal (15M entry, higher-TF confirmed, MA200 aligned)");
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
