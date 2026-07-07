//+------------------------------------------------------------------+
//|                                         GoldSessionBreakout.mq5   |
//|            Trend-filtered Session Breakout EA - MT5 (MQL5)        |
//|                                                                  |
//|  Idea: Gold builds a range during the quiet Asian session, then  |
//|  breaks out directionally as London/NY open. This EA captures    |
//|  that range, then takes the breakout ONLY in the direction of    |
//|  the higher-timeframe trend, with ATR-based stops, a fixed R:R,   |
//|  breakeven + ATR trailing, and risk-based position sizing.       |
//|                                                                  |
//|  NOTE: session hours are in BROKER SERVER TIME - adjust the      |
//|  Range/Trade hours to your broker (check the clock in Market     |
//|  Watch). Backtest in the Strategy Tester before trusting it.     |
//+------------------------------------------------------------------+
#property copyright "Claude AI"
#property link      "https://www.mql5.com"
#property version   "1.00"

#include <Trade\Trade.mqh>

//==================== SESSION / RANGE (server time) ===============
input ENUM_TIMEFRAMES  Range_TF             = PERIOD_M15; // Timeframe used to build the range
input int              RangeStartHour       = 1;          // Range build START hour (server)
input int              RangeStartMin        = 0;          // Range build START minute
input int              RangeEndHour         = 8;          // Range build END hour (server)
input int              RangeEndMin          = 0;          // Range build END minute
input int              TradeEndHour         = 20;         // No NEW entries after this hour (server)
input double           Breakout_Buffer_ATR  = 0.10;       // Break must exceed range by this x ATR

//==================== TREND FILTER ================================
input bool             Use_Trend_Filter     = true;       // Only trade with the higher-TF trend
input ENUM_TIMEFRAMES  Trend_TF             = PERIOD_D1;  // Trend timeframe
input int              Trend_MA_Period      = 50;         // Trend MA period
input ENUM_MA_METHOD   Trend_MA_Method      = MODE_EMA;   // Trend MA method

//==================== ATR (stops & buffer) =======================
input ENUM_TIMEFRAMES  ATR_TF               = PERIOD_H1;  // ATR timeframe
input int              ATR_Period           = 14;         // ATR period

//==================== RISK / STOPS ===============================
input double           SL_ATR_Mult          = 1.5;        // Stop Loss = this x ATR
input double           RR_Ratio             = 2.0;        // Take Profit = RR x stop distance
input bool             Risk_Type_IsPercent  = false;      // true=% of balance, false=fixed money
input double           Risk_Value           = 5.0;        // Risk per trade (% or money)

//==================== RANGE SIZE FILTERS (0 = off) ================
input double           Range_Min_ATR        = 0.0;        // Skip if range < this x ATR (too quiet)
input double           Range_Max_ATR        = 0.0;        // Skip if range > this x ATR (too extended)

//==================== BREAKEVEN / TRAILING =======================
input bool             Use_Breakeven        = true;       // Move SL to entry once in profit
input double           BE_Trigger_ATR       = 1.0;        // Breakeven after profit >= this x ATR
input bool             Use_Trailing         = true;       // Trail SL after further profit
input double           Trail_Trigger_ATR    = 1.5;        // Start trailing after profit >= this x ATR
input double           Trail_Dist_ATR       = 1.0;        // Keep SL this x ATR behind price

//==================== GENERAL ====================================
input int              MagicNumber          = 333333;     // Magic Number
input int              Slippage             = 20;         // Slippage (points)
input int              Max_Trades_Per_Day   = 2;          // Max trades/day (allows 1 long + 1 short)
input string           TradeComment         = "GoldSession"; // Order comment

//==================== GLOBALS ====================================
CTrade   trade;
int      atr_handle    = INVALID_HANDLE;
int      trend_handle  = INVALID_HANDLE;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   ConfigureFillingMode();

   atr_handle = iATR(_Symbol, ATR_TF, ATR_Period);
   if(atr_handle == INVALID_HANDLE)
   {
      Print("Failed to create ATR handle");
      return INIT_FAILED;
   }

   if(Use_Trend_Filter)
   {
      trend_handle = iMA(_Symbol, Trend_TF, Trend_MA_Period, 0, Trend_MA_Method, PRICE_CLOSE);
      if(trend_handle == INVALID_HANDLE)
      {
         Print("Failed to create Trend MA handle");
         return INIT_FAILED;
      }
   }

   Print("=== Gold Session Breakout EA Started ===");
   PrintFormat("Range %02d:%02d-%02d:%02d (server) | TradeEnd=%02d:00 | Trend=%s | SL=%.1fxATR RR=%.1f | Risk=%.2f%s",
               RangeStartHour, RangeStartMin, RangeEndHour, RangeEndMin, TradeEndHour,
               Use_Trend_Filter ? "on" : "off", SL_ATR_Mult, RR_Ratio,
               Risk_Value, Risk_Type_IsPercent ? "%" : " money");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(atr_handle   != INVALID_HANDLE) IndicatorRelease(atr_handle);
   if(trend_handle != INVALID_HANDLE) IndicatorRelease(trend_handle);
   Print("=== Gold Session Breakout EA Stopped ===");
}

//+------------------------------------------------------------------+
void OnTick()
{
   //--- Always manage open trades (breakeven + trailing)
   ManageOpenTrades();

   double atr = GetATR();
   if(atr <= 0)
      return;

   //--- Entries only inside the trading window (after range, before cutoff)
   datetime now      = TimeCurrent();
   datetime dayStart = StartOfDay(now);
   datetime rEnd     = dayStart + RangeEndHour * 3600 + RangeEndMin * 60;
   datetime tEnd     = dayStart + TradeEndHour * 3600;
   if(now < rEnd || now >= tEnd)
      return;

   //--- Build today's range
   double rangeHigh, rangeLow;
   if(!GetTodayRange(rangeHigh, rangeLow))
      return;

   double rangeWidth = rangeHigh - rangeLow;
   if(Range_Min_ATR > 0 && rangeWidth < Range_Min_ATR * atr) return;
   if(Range_Max_ATR > 0 && rangeWidth > Range_Max_ATR * atr) return;

   double buffer = Breakout_Buffer_ATR * atr;
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   //--- Buy breakout above the range
   if(ask > rangeHigh + buffer)
   {
      if(TrendOK(true) && CountDirToday(true) == 0 && CanOpenNewPosition())
         OpenTrade(true, atr);
   }
   //--- Sell breakout below the range
   else if(bid < rangeLow - buffer)
   {
      if(TrendOK(false) && CountDirToday(false) == 0 && CanOpenNewPosition())
         OpenTrade(false, atr);
   }
}

//+------------------------------------------------------------------+
//| Build today's range from Range_TF bars between the session hours |
//| Recomputed from bars each call => fully restart-safe.            |
//+------------------------------------------------------------------+
bool GetTodayRange(double &rangeHigh, double &rangeLow)
{
   datetime dayStart = StartOfDay(TimeCurrent());
   datetime rStart   = dayStart + RangeStartHour * 3600 + RangeStartMin * 60;
   datetime rEnd     = dayStart + RangeEndHour   * 3600 + RangeEndMin   * 60;
   if(TimeCurrent() < rEnd)
      return false;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, Range_TF, rStart, rEnd, rates);
   if(copied <= 0)
      return false;

   rangeHigh = -DBL_MAX;
   rangeLow  =  DBL_MAX;
   for(int i = 0; i < copied; i++)
   {
      rangeHigh = MathMax(rangeHigh, rates[i].high);
      rangeLow  = MathMin(rangeLow,  rates[i].low);
   }
   return (rangeHigh > rangeLow);
}

//+------------------------------------------------------------------+
bool TrendOK(bool isBuy)
{
   if(!Use_Trend_Filter)
      return true;

   double ma = GetBufferValue(trend_handle, 1);   // last closed trend bar
   if(ma <= 0)
      return true;   // cannot evaluate yet - do not block

   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return isBuy ? (price > ma) : (price < ma);
}

//+------------------------------------------------------------------+
void OpenTrade(bool isBuy, double atr)
{
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double price  = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double slDist = SL_ATR_Mult * atr;
   if(slDist <= 0)
      return;

   double sl = isBuy ? price - slDist : price + slDist;
   double tp = isBuy ? price + RR_Ratio * slDist : price - RR_Ratio * slDist;
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   double lots = CalculateLotSize(slDist);

   bool ok = isBuy ? trade.Buy (lots, _Symbol, price, sl, tp, TradeComment)
                   : trade.Sell(lots, _Symbol, price, sl, tp, TradeComment);

   if(ok)
      PrintFormat("%s breakout @ %.3f SL=%.3f TP=%.3f lots=%.2f (ATR=%.3f)",
                  isBuy ? "BUY" : "SELL", price, sl, tp, lots, atr);
   else
      PrintFormat("%s failed: %s", isBuy ? "BUY" : "SELL", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Lot size from risk (money or % of balance) and the SL distance   |
//+------------------------------------------------------------------+
double CalculateLotSize(double slDistPrice)
{
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(slDistPrice <= 0 || tickValue <= 0 || tickSize <= 0)
      return minLot;

   double riskMoney = Risk_Type_IsPercent
                      ? AccountInfoDouble(ACCOUNT_BALANCE) * Risk_Value / 100.0
                      : Risk_Value;

   double slTicks    = slDistPrice / tickSize;
   double riskPerLot = slTicks * tickValue;
   if(riskPerLot <= 0)
      return minLot;

   double lots = riskMoney / riskPerLot;

   lots = MathFloor(lots / stepLot) * stepLot;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Breakeven + ATR trailing for this EA's open positions            |
//+------------------------------------------------------------------+
void ManageOpenTrades()
{
   if(!Use_Breakeven && !Use_Trailing)
      return;

   double atr = GetATR();
   if(atr <= 0)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL     = PositionGetDouble(POSITION_SL);
      double curTP     = PositionGetDouble(POSITION_TP);
      bool   isBuy     = (type == POSITION_TYPE_BUY);

      double price  = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                            : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double profit = isBuy ? (price - openPrice) : (openPrice - price);

      if(isBuy)
      {
         double desired = curSL;   // want to raise SL
         if(Use_Breakeven && profit >= BE_Trigger_ATR * atr)
            desired = MathMax(desired, openPrice);
         if(Use_Trailing && profit >= Trail_Trigger_ATR * atr)
            desired = MathMax(desired, price - Trail_Dist_ATR * atr);

         desired = NormalizeDouble(desired, digits);
         if(desired > curSL && desired < price)
            trade.PositionModify(ticket, desired, curTP);
      }
      else
      {
         double desired = (curSL == 0) ? DBL_MAX : curSL;   // want to lower SL
         if(Use_Breakeven && profit >= BE_Trigger_ATR * atr)
            desired = MathMin(desired, openPrice);
         if(Use_Trailing && profit >= Trail_Trigger_ATR * atr)
            desired = MathMin(desired, price + Trail_Dist_ATR * atr);

         if(desired != DBL_MAX)
         {
            desired = NormalizeDouble(desired, digits);
            double effectiveCur = (curSL == 0) ? DBL_MAX : curSL;
            if(desired < effectiveCur && desired > price)
               trade.PositionModify(ticket, desired, curTP);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Guards                                                           |
//+------------------------------------------------------------------+
bool CanOpenNewPosition()
{
   if(CountMyPositions() > 0)          // one position at a time (this EA)
      return false;

   if(Max_Trades_Per_Day > 0 && CountTradesToday() >= Max_Trades_Per_Day)
      return false;

   return true;
}

//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Count this EA's entries today (optionally by direction).         |
//| From deal history => restart-safe, resets each new day.          |
//+------------------------------------------------------------------+
int CountTradesToday()
{
   return CountEntriesToday(false, false);
}

int CountDirToday(bool isBuy)
{
   return CountEntriesToday(true, isBuy);
}

int CountEntriesToday(bool byDirection, bool isBuy)
{
   datetime dayStart = StartOfDay(TimeCurrent());
   if(!HistorySelect(dayStart, TimeCurrent() + 1))
      return 0;

   int count = 0;
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;

      if(byDirection)
      {
         long dtype = HistoryDealGetInteger(ticket, DEAL_TYPE);
         if(isBuy  && dtype != DEAL_TYPE_BUY)  continue;
         if(!isBuy && dtype != DEAL_TYPE_SELL) continue;
      }
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
datetime StartOfDay(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   dt.hour = 0;
   dt.min  = 0;
   dt.sec  = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
double GetATR()
{
   return GetBufferValue(atr_handle, 1);   // last closed ATR value
}

//+------------------------------------------------------------------+
double GetBufferValue(int handle, int shift)
{
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, 0, shift, 1, buf) < 1)
      return -1;
   return buf[0];
}

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
