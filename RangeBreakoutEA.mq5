//+------------------------------------------------------------------+
//|                                              RangeBreakoutEA.mq5  |
//|                     Range Breakout EA (MetaTrader 5 / MQL5)       |
//+------------------------------------------------------------------+
#property copyright "Claude AI"
#property link      "https://www.mql5.com"
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- Enums
enum ENUM_VOLUME_MODE
{
   VOLUME_FIXED,    // Fixed lot size
   VOLUME_MANAGED,  // Fixed Lots per each Fixed Lots Per x Money
   VOLUME_PERCENT,  // Lots sized so SL hit loses % of balance
   VOLUME_MONEY     // Lots sized so SL hit loses a fixed money amount
};

enum ENUM_CALC_MODE
{
   CALC_MODE_OFF,      // No TP/SL placed
   CALC_MODE_FACTOR,   // Distance = multiple of the range size
   CALC_MODE_PERCENT,  // Distance = % of range high (buy) / range low (sell)
   CALC_MODE_POINTS    // Distance in points
};

enum ENUM_TRAIL_MODE
{
   TSL_MODE_OFF,      // Inactive
   TSL_MODE_PERCENT,  // Distance = % of position open price
   TSL_MODE_POINTS    // Distance in points
};

//--- General Settings
input ENUM_TIMEFRAMES  RangeTimeframe        = PERIOD_M1;        // Timeframe for range calc (use M1 live)
input ENUM_VOLUME_MODE TradingVolume         = VOLUME_FIXED;     // Trading volume mode
input double           FixedLots             = 0.01;             // Fixed lot size
input double           FixedLotsPerMoney     = 1000;             // Fixed Lots Per x Money
input double           RiskPercentOfBalance  = 1.0;              // Risk % of balance (VOLUME_PERCENT)
input double           RiskMoney             = 100;              // Risk money (VOLUME_MONEY)
input int              OrderBufferPoints     = 0;                // Buffer above/below range, in points
input ENUM_CALC_MODE   TargetCalcMode        = CALC_MODE_POINTS; // TP calc mode
input double           TargetValue           = 3000;             // TP value ($30 at 0.01 lots)
input ENUM_CALC_MODE   StopCalcMode          = CALC_MODE_POINTS; // SL calc mode
input double           StopValue             = 2000;             // SL value ($20 at 0.01 lots)

//--- Time Settings
input int  RangeStartHour       = 6;     // Range start hour
input int  RangeStartMinute     = 0;     // Range start minute
input int  RangeEndHour         = 8;     // Range end hour
input int  RangeEndMinute       = 0;     // Range end minute
input int  DeleteOrdersHour     = 23;    // Hour unfilled pending orders expire
input int  DeleteOrdersMinute   = 0;     // Minute unfilled pending orders expire
input bool ClosePositions       = false; // Close open positions at the close time
input int  ClosePositionsHour   = 23;    // Hour positions are closed
input int  ClosePositionsMinute = 30;    // Minute positions are closed

//--- Trailing Stop Settings
input ENUM_TRAIL_MODE BEStopCalcMode    = TSL_MODE_OFF; // Break-even calc mode
input double          BEStopTriggerValue = 50;          // BE activates above this profit
input double          BEStopBufferValue  = 10;          // SL moved this far into profit at BE
input ENUM_TRAIL_MODE TSLCalcMode         = TSL_MODE_OFF; // Classic trailing stop calc mode
input double          TSLTriggerValue     = 50;          // TSL activates above this profit
input double          TSLValue            = 30;          // Distance kept behind market price
input double          TSLStepValue        = 5;           // Minimum SL improvement to re-modify

//--- Trading Frequency Settings
input int MaxLongTrades  = 2; // Max buy trades per day
input int MaxShortTrades = 2; // Max sell trades per day
input int MaxTotalTrades = 3; // Max total trades per day

//--- Range Filter Settings
input double MinRangePoints  = 0; // Ignore ranges smaller than this (points, 0 = off)
input double MinRangePercent = 0; // Ignore ranges smaller than this (%, 0 = off)
input double MaxRangePoints  = 0; // Ignore ranges larger than this (points, 0 = off)
input double MaxRangePercent = 0; // Ignore ranges larger than this (%, 0 = off)

//--- More Settings
input color  RangeColor   = clrDodgerBlue;     // Color of the drawn range box
input string OrderComment = "RangeBreakout";   // Comment attached to every order
input int    MagicNumber  = 778899;            // Unique number identifying this EA's trades
input bool   ChartComment = true;              // Show info comment on chart
input bool   DebugMode    = false;             // Print extra diagnostics to Journal

CTrade trade;

datetime g_rangeDay             = 0;
double   g_rangeHigh            = 0;
double   g_rangeLow             = 0;
bool     g_rangeComputed        = false; // high/low captured for today
bool     g_rangeReady           = false; // captured AND passed the range filters
bool     g_ordersDeletedToday   = false;
bool     g_positionsClosedToday = false;
int      g_longTradesToday      = 0;
int      g_shortTradesToday     = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   ConfigureFillingMode();

   Print("=== Range Breakout EA Started ===");
   PrintFormat("Range window: %02d:%02d - %02d:%02d | Delete orders: %02d:%02d | Close positions: %s %02d:%02d",
               RangeStartHour, RangeStartMinute, RangeEndHour, RangeEndMinute,
               DeleteOrdersHour, DeleteOrdersMinute,
               ClosePositions ? "on" : "off", ClosePositionsHour, ClosePositionsMinute);
   PrintFormat("Max trades/day -> long=%d short=%d total=%d", MaxLongTrades, MaxShortTrades, MaxTotalTrades);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
   Print("=== Range Breakout EA Stopped ===");
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);
   datetime today = StartOfDay(now);

   if(today != g_rangeDay)
      ResetDailyState(today);

   ManageTrailing();
   UpdateRange(now, dt);

   if(g_rangeReady)
      MaintainPendingOrders(now, dt);

   CheckDeleteOrders(now, dt);
   CheckClosePositions(now, dt);

   if(ChartComment)
      UpdateChartComment();
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
datetime StartOfDay(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
void ResetDailyState(datetime today)
{
   g_rangeDay             = today;
   g_rangeComputed        = false;
   g_rangeReady           = false;
   g_rangeHigh            = 0;
   g_rangeLow             = 0;
   g_ordersDeletedToday   = false;
   g_positionsClosedToday = false;
   RefreshDailyCounts(today);
}

//+------------------------------------------------------------------+
//| Recompute today's filled trade counts from broker history, so    |
//| daily limits survive an EA/terminal restart mid-day.              |
//+------------------------------------------------------------------+
void RefreshDailyCounts(datetime today)
{
   g_longTradesToday  = 0;
   g_shortTradesToday = 0;

   if(!HistorySelect(today, today + 86400))
      return;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;

      long type = HistoryDealGetInteger(ticket, DEAL_TYPE);
      if(type == DEAL_TYPE_BUY)  g_longTradesToday++;
      if(type == DEAL_TYPE_SELL) g_shortTradesToday++;
   }
}

//+------------------------------------------------------------------+
//| Capture the day's range once the range window has closed          |
//| (assumes RangeStart < RangeEnd within the same calendar day)      |
//+------------------------------------------------------------------+
void UpdateRange(datetime now, MqlDateTime &dt)
{
   if(g_rangeComputed)
      return;

   int nowMin = dt.hour * 60 + dt.min;
   int endMin = RangeEndHour * 60 + RangeEndMinute;
   if(nowMin < endMin)
      return;

   int barCount = 500;
   MqlRates rates[];
   int copied = CopyRates(_Symbol, RangeTimeframe, 0, barCount, rates);
   if(copied <= 0)
   {
      if(DebugMode) Print("[debug] No rate data available");
      return;
   }

   double high = 0, low = 1e10;
   for(int i = 0; i < copied; i++)
   {
      if(rates[i].time < g_rangeDay + RangeStartHour * 3600) continue;
      if(rates[i].time >= g_rangeDay + RangeEndHour * 3600) break;
      if(rates[i].high > high) high = rates[i].high;
      if(rates[i].low < low) low = rates[i].low;
   }

   if(high == 0 || low == 1e10)
   {
      if(DebugMode) Print("[debug] No data in range window");
      return;
   }

   g_rangeHigh     = high;
   g_rangeLow      = low;
   g_rangeComputed = true;
   g_rangeReady    = PassesRangeFilter(high - low, high);

   DrawRangeBox(rangeStart, rangeEnd, high, low);

   if(DebugMode)
      PrintFormat("[debug] Range captured: high=%.5f low=%.5f size=%.1f pts ready=%s",
                  high, low, (high - low) / _Point, g_rangeReady ? "true" : "false");
}

//+------------------------------------------------------------------+
bool PassesRangeFilter(double rangeSize, double rangeHigh)
{
   double rangePoints  = rangeSize / _Point;
   double rangePercent = (rangeHigh != 0) ? (rangeSize / rangeHigh) * 100.0 : 0;

   if(MinRangePoints  > 0 && rangePoints  < MinRangePoints)  return false;
   if(MinRangePercent > 0 && rangePercent < MinRangePercent) return false;
   if(MaxRangePoints  > 0 && rangePoints  > MaxRangePoints)  return false;
   if(MaxRangePercent > 0 && rangePercent > MaxRangePercent) return false;
   return true;
}

//+------------------------------------------------------------------+
void DrawRangeBox(datetime t1, datetime t2, double high, double low)
{
   string name = "RangeBreakoutBox_" + TimeToString(g_rangeDay, TIME_DATE);
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, high, t2, low);
   ObjectSetInteger(0, name, OBJPROP_COLOR, RangeColor);
   ObjectSetInteger(0, name, OBJPROP_FILL, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
}

//+------------------------------------------------------------------+
//| Arm/maintain the breakout pending orders for today                |
//+------------------------------------------------------------------+
void MaintainPendingOrders(datetime now, MqlDateTime &dt)
{
   int nowMin    = dt.hour * 60 + dt.min;
   int deleteMin = DeleteOrdersHour * 60 + DeleteOrdersMinute;
   if(nowMin >= deleteMin)
      return;

   RefreshDailyCounts(g_rangeDay);

   bool hasLongPos  = HasPosition(POSITION_TYPE_BUY);
   bool hasShortPos = HasPosition(POSITION_TYPE_SELL);
   bool hasBuyStop  = HasPendingOrder(ORDER_TYPE_BUY_STOP);
   bool hasSellStop = HasPendingOrder(ORDER_TYPE_SELL_STOP);

   // Once one side fills, cancel the still-pending opposite order (OCO behaviour)
   if(hasLongPos  && hasSellStop) DeletePendingOrdersOfType(ORDER_TYPE_SELL_STOP);
   if(hasShortPos && hasBuyStop)  DeletePendingOrdersOfType(ORDER_TYPE_BUY_STOP);

   bool totalMaxed = (g_longTradesToday + g_shortTradesToday) >= MaxTotalTrades;

   if(!hasLongPos && !HasPendingOrder(ORDER_TYPE_BUY_STOP) && !totalMaxed && g_longTradesToday < MaxLongTrades)
      PlaceBuyStop();

   if(!hasShortPos && !HasPendingOrder(ORDER_TYPE_SELL_STOP) && !totalMaxed && g_shortTradesToday < MaxShortTrades)
      PlaceSellStop();
}

//+------------------------------------------------------------------+
bool HasPosition(ENUM_POSITION_TYPE type)
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetInteger(POSITION_TYPE) == type) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
bool HasPendingOrder(ENUM_ORDER_TYPE type)
{
   for(int i = 0; i < OrdersTotal(); i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) == type) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
void DeletePendingOrdersOfType(ENUM_ORDER_TYPE type)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) != type) continue;
      trade.OrderDelete(ticket);
   }
}

//+------------------------------------------------------------------+
void DeleteAllPendingOrders()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      trade.OrderDelete(ticket);
   }
}

//+------------------------------------------------------------------+
double CalcDistance(ENUM_CALC_MODE mode, double value, double rangeSize, double refPrice)
{
   switch(mode)
   {
      case CALC_MODE_OFF:     return 0;
      case CALC_MODE_FACTOR:  return value * rangeSize;
      case CALC_MODE_PERCENT: return refPrice * value / 100.0;
      case CALC_MODE_POINTS:  return value * _Point;
   }
   return 0;
}

//+------------------------------------------------------------------+
void ComputeBuyLevels(double &entry, double &sl, double &tp)
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double rangeSize = g_rangeHigh - g_rangeLow;

   entry = NormalizeDouble(g_rangeHigh + OrderBufferPoints * _Point, digits);

   double slDist = CalcDistance(StopCalcMode,   StopValue,   rangeSize, g_rangeHigh);
   double tpDist = CalcDistance(TargetCalcMode, TargetValue, rangeSize, g_rangeHigh);

   sl = (StopCalcMode   == CALC_MODE_OFF) ? 0 : NormalizeDouble(entry - slDist, digits);
   tp = (TargetCalcMode == CALC_MODE_OFF) ? 0 : NormalizeDouble(entry + tpDist, digits);
}

//+------------------------------------------------------------------+
void ComputeSellLevels(double &entry, double &sl, double &tp)
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double rangeSize = g_rangeHigh - g_rangeLow;

   entry = NormalizeDouble(g_rangeLow - OrderBufferPoints * _Point, digits);

   double slDist = CalcDistance(StopCalcMode,   StopValue,   rangeSize, g_rangeLow);
   double tpDist = CalcDistance(TargetCalcMode, TargetValue, rangeSize, g_rangeLow);

   sl = (StopCalcMode   == CALC_MODE_OFF) ? 0 : NormalizeDouble(entry + slDist, digits);
   tp = (TargetCalcMode == CALC_MODE_OFF) ? 0 : NormalizeDouble(entry - tpDist, digits);
}

//+------------------------------------------------------------------+
double CalculateVolume(double slDistance)
{
   double lots    = FixedLots;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   switch(TradingVolume)
   {
      case VOLUME_FIXED:
         lots = FixedLots;
         break;

      case VOLUME_MANAGED:
         lots = (FixedLotsPerMoney > 0) ? FixedLots * (balance / FixedLotsPerMoney) : FixedLots;
         break;

      case VOLUME_PERCENT:
      case VOLUME_MONEY:
      {
         double riskMoney = (TradingVolume == VOLUME_PERCENT) ? balance * RiskPercentOfBalance / 100.0 : RiskMoney;
         double tickValue  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
         double tickSize   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

         if(slDistance <= 0 || tickSize <= 0 || tickValue <= 0)
            lots = FixedLots; // can't size off a zero SL distance, fall back to fixed
         else
            lots = riskMoney / (slDistance / tickSize * tickValue);
         break;
      }
   }

   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   lots = MathFloor(lots / stepLot) * stepLot;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
void PlaceBuyStop()
{
   double entry, sl, tp;
   ComputeBuyLevels(entry, sl, tp);
   double slDist = (sl > 0) ? entry - sl : 0;
   double lots   = CalculateVolume(slDist);

   if(trade.BuyStop(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, OrderComment))
      PrintFormat("BuyStop placed @ %.5f sl=%.5f tp=%.5f lots=%.2f", entry, sl, tp, lots);
   else
      Print("BuyStop failed: ", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
void PlaceSellStop()
{
   double entry, sl, tp;
   ComputeSellLevels(entry, sl, tp);
   double slDist = (sl > 0) ? sl - entry : 0;
   double lots   = CalculateVolume(slDist);

   if(trade.SellStop(lots, entry, _Symbol, sl, tp, ORDER_TIME_GTC, 0, OrderComment))
      PrintFormat("SellStop placed @ %.5f sl=%.5f tp=%.5f lots=%.2f", entry, sl, tp, lots);
   else
      Print("SellStop failed: ", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Delete unfilled pending orders once the cutoff time is reached    |
//+------------------------------------------------------------------+
void CheckDeleteOrders(datetime now, MqlDateTime &dt)
{
   if(g_ordersDeletedToday) return;

   int nowMin = dt.hour * 60 + dt.min;
   int delMin = DeleteOrdersHour * 60 + DeleteOrdersMinute;
   if(nowMin >= delMin)
   {
      DeleteAllPendingOrders();
      g_ordersDeletedToday = true;
      if(DebugMode) Print("[debug] Deleted unfilled pending orders for today");
   }
}

//+------------------------------------------------------------------+
//| Close all open positions once the close-time cutoff is reached    |
//+------------------------------------------------------------------+
void CheckClosePositions(datetime now, MqlDateTime &dt)
{
   if(!ClosePositions || g_positionsClosedToday) return;

   int nowMin   = dt.hour * 60 + dt.min;
   int closeMin = ClosePositionsHour * 60 + ClosePositionsMinute;
   if(nowMin < closeMin) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      trade.PositionClose(ticket);
   }
   g_positionsClosedToday = true;
   if(DebugMode) Print("[debug] Closed all open positions for today");
}

//+------------------------------------------------------------------+
double TrailUnitToPrice(ENUM_TRAIL_MODE mode, double value, double openPrice)
{
   if(mode == TSL_MODE_POINTS)  return value * _Point;
   if(mode == TSL_MODE_PERCENT) return openPrice * value / 100.0;
   return 0;
}

//+------------------------------------------------------------------+
//| Break-even + classic trailing stop, in points or % of open price  |
//+------------------------------------------------------------------+
void ManageTrailing()
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL      = PositionGetDouble(POSITION_SL);
      double curTP      = PositionGetDouble(POSITION_TP);
      double price      = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                                        : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double profit     = (type == POSITION_TYPE_BUY) ? price - openPrice : openPrice - price;

      double newSL = curSL;

      if(BEStopCalcMode != TSL_MODE_OFF)
      {
         double trigger = TrailUnitToPrice(BEStopCalcMode, BEStopTriggerValue, openPrice);
         double buffer  = TrailUnitToPrice(BEStopCalcMode, BEStopBufferValue,  openPrice);
         if(profit >= trigger)
         {
            double beSL = (type == POSITION_TYPE_BUY) ? openPrice + buffer : openPrice - buffer;
            if(type == POSITION_TYPE_BUY  && (curSL == 0 || beSL > newSL)) newSL = beSL;
            if(type == POSITION_TYPE_SELL && (curSL == 0 || beSL < newSL || newSL == 0)) newSL = beSL;
         }
      }

      if(TSLCalcMode != TSL_MODE_OFF)
      {
         double trigger = TrailUnitToPrice(TSLCalcMode, TSLTriggerValue, openPrice);
         double dist    = TrailUnitToPrice(TSLCalcMode, TSLValue,        openPrice);
         double step    = TrailUnitToPrice(TSLCalcMode, TSLStepValue,    openPrice);
         if(profit >= trigger)
         {
            double tslSL = (type == POSITION_TYPE_BUY) ? price - dist : price + dist;
            if(type == POSITION_TYPE_BUY  && tslSL > newSL + step) newSL = tslSL;
            if(type == POSITION_TYPE_SELL && (newSL == 0 || tslSL < newSL - step)) newSL = tslSL;
         }
      }

      newSL = NormalizeDouble(newSL, digits);
      if(newSL != 0 && newSL != NormalizeDouble(curSL, digits))
      {
         if(trade.PositionModify(ticket, newSL, curTP) && DebugMode)
            PrintFormat("[trail] ticket %I64u SL -> %.5f", ticket, newSL);
      }
   }
}

//+------------------------------------------------------------------+
void UpdateChartComment()
{
   string txt = StringFormat(
      "Range Breakout EA\nRange: %.5f - %.5f (%.1f pts)\nReady: %s\nLong today: %d/%d   Short today: %d/%d\nTotal today: %d/%d",
      g_rangeHigh, g_rangeLow, (g_rangeHigh - g_rangeLow) / _Point,
      g_rangeReady ? "yes" : "no",
      g_longTradesToday, MaxLongTrades,
      g_shortTradesToday, MaxShortTrades,
      g_longTradesToday + g_shortTradesToday, MaxTotalTrades);
   Comment(txt);
}
