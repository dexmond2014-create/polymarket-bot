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
input int    StopLoss        = 30;     // Stop loss, in points
input int    BreakevenProfit = 20;     // Move SL to breakeven after this many points of profit
input int    RetraceProfit   = 20;     // Close trade if profit retraces this many points from its peak
input bool   UseNewsFilter   = true;   // Pause new entries during news hours
input int    MagicNumber     = 123456;
input int    MaxTrades       = 2;      // Max simultaneous open positions for this EA

CTrade trade;

int hAlligator30M = INVALID_HANDLE;
int hAlligatorH4 = INVALID_HANDLE;
int hAlligatorD1 = INVALID_HANDLE;
int hAlligatorW1 = INVALID_HANDLE;

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

   hAlligator30M = iAlligator(_Symbol, PERIOD_M30, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorH4 = iAlligator(_Symbol, PERIOD_H4, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorD1 = iAlligator(_Symbol, PERIOD_D1, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);
   hAlligatorW1 = iAlligator(_Symbol, PERIOD_W1, 13, 8, 8, 5, 5, 3, MODE_SMMA, PRICE_MEDIAN);

   if(hAlligator30M == INVALID_HANDLE || hAlligatorH4 == INVALID_HANDLE ||
      hAlligatorD1 == INVALID_HANDLE || hAlligatorW1 == INVALID_HANDLE)
   {
      Print("Failed to create one or more Alligator indicator handles");
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
   if(hAlligator30M != INVALID_HANDLE) IndicatorRelease(hAlligator30M);
   if(hAlligatorH4 != INVALID_HANDLE) IndicatorRelease(hAlligatorH4);
   if(hAlligatorD1 != INVALID_HANDLE) IndicatorRelease(hAlligatorD1);
   if(hAlligatorW1 != INVALID_HANDLE) IndicatorRelease(hAlligatorW1);
   Print("=== Bot Stopped ===");
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
datetime g_lastBarTime = 0;

void OnTick()
{
   ManageOpenPositions();

   // Print a status snapshot once per new 30M bar (for debugging)
   datetime barTime = iTime(_Symbol, PERIOD_M30, 0);
   if(barTime != g_lastBarTime)
   {
      g_lastBarTime = barTime;
      PrintAlligatorStatus();
   }

   if(CountMyPositions() < MaxTrades)
      CheckAlligatorSetup();
}

//+------------------------------------------------------------------+
//| Debug: print Alligator readings + trend flags for all timeframes  |
//+------------------------------------------------------------------+
void PrintAlligatorStatus()
{
   double jaw30M, teeth30M, lips30M;
   bool ok30M = GetAlligator(hAlligator30M, jaw30M, teeth30M, lips30M);

   bool up4H = IsUptrendAlligator(hAlligatorH4);
   bool upD1 = IsUptrendAlligator(hAlligatorD1);
   bool upW1 = IsUptrendAlligator(hAlligatorW1);

   bool dn4H = IsDowntrendAlligator(hAlligatorH4);
   bool dnD1 = IsDowntrendAlligator(hAlligatorD1);
   bool dnW1 = IsDowntrendAlligator(hAlligatorW1);

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(!ok30M)
   {
      Print("[status] 30M Alligator data not ready yet (not enough history)");
   }
   else
   {
      PrintFormat("[status] 30M jaw=%.5f teeth=%.5f lips=%.5f bid=%.5f bid>lips=%s",
                  jaw30M, teeth30M, lips30M, bid, (bid > lips30M ? "true" : "false"));
   }

   PrintFormat("[status] H4 up=%s down=%s | D1 up=%s down=%s | W1 up=%s down=%s",
               up4H ? "true" : "false", dn4H ? "true" : "false",
               upD1 ? "true" : "false", dnD1 ? "true" : "false",
               upW1 ? "true" : "false", dnW1 ? "true" : "false");

   if(!up4H && !dn4H) Print("[status] H4 Alligator: no data / no clear trend (lines not fanned)");
   if(!upD1 && !dnD1) Print("[status] D1 Alligator: no data / no clear trend (lines not fanned)");
   if(!upW1 && !dnW1) Print("[status] W1 Alligator: no data / no clear trend (lines not fanned)");
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
//| Multi-timeframe Alligator entry check                              |
//+------------------------------------------------------------------+
void CheckAlligatorSetup()
{
   if(IsMajorNewsTime())
      return;

   double jaw30M, teeth30M, lips30M;
   if(!GetAlligator(hAlligator30M, jaw30M, teeth30M, lips30M))
      return;

   bool uptrend4H   = IsUptrendAlligator(hAlligatorH4);
   bool uptrendD1   = IsUptrendAlligator(hAlligatorD1);
   bool uptrendW1   = IsUptrendAlligator(hAlligatorW1);

   bool downtrend4H = IsDowntrendAlligator(hAlligatorH4);
   bool downtrendD1 = IsDowntrendAlligator(hAlligatorD1);
   bool downtrendW1 = IsDowntrendAlligator(hAlligatorW1);

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(bid > lips30M && uptrend4H && uptrendD1 && uptrendW1)
   {
      Print("BUY signal (30M entry, H4/D1/W1 confirmed)");
      OpenBuy();
      return;
   }

   if(ask < lips30M && downtrend4H && downtrendD1 && downtrendW1)
   {
      Print("SELL signal (30M entry, H4/D1/W1 confirmed)");
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
         ArrayRemove(g_positions, i, 1);
      }
   }
}
