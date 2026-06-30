//+------------------------------------------------------------------+
//|                                      ATR_CandleBreakout_EUR.mq5  |
//|              ATR Candle Breakout EA  -  MT5 (MQL5) Only          |
//|  Large-candle breakout vs ATR, with optional trend / MTF-ATR /   |
//|  time-of-day / support-resistance filters and risk-based sizing. |
//|  Default inputs below are tuned as a STARTING POINT for EURUSD.  |
//|  For GOLD (XAUUSD) use the ATR_CandleBreakout.mq5 build instead. |
//+------------------------------------------------------------------+
#property copyright "Claude AI"
#property link      "https://www.mql5.com"
#property version   "1.02"

#include <Trade\Trade.mqh>

//==================== STRATEGY SETTINGS ============================
input ENUM_TIMEFRAMES  Signal_TF            = PERIOD_M15; // Signal Timeframe
input int              ATR_Period           = 14;         // ATR Period
input double           ATR_Multiplier       = 1.5;        // Signal candle must be > ATR x this
input double           Close_Proximity_Pct  = 25.0;       // Close proximity to candle extreme (% of range)
input double           Min_Body_Ratio_Pct   = 50.0;       // Min candle body-to-range ratio % (0 = off)

//==================== TREND FILTER ================================
input bool             Use_Trend_Filter     = true;       // Enable trend filter
input ENUM_TIMEFRAMES  Trend_TF             = PERIOD_H4;  // Trend timeframe
input int              Trend_MA_Period      = 50;         // Trend MA period
input ENUM_MA_METHOD   Trend_MA_Method      = MODE_EMA;   // Trend MA method

//==================== MTF ATR CONFIRMATION =======================
input bool             Use_MTF_ATR          = false;      // Enable MTF ATR confirmation
input ENUM_TIMEFRAMES  HTF                  = PERIOD_H1;  // Higher timeframe for ATR
input int              HTF_ATR_Period       = 14;         // Higher TF ATR period
input double           HTF_ATR_Multiplier   = 1.0;        // HTF candle must be > ATR x this

//==================== TIME FILTER ================================
input bool             Use_Time_Filter      = false;      // Enable time-of-day filter
input int              Trade_Start_Hour     = 7;          // Trading start hour (server time)
input int              Trade_End_Hour       = 21;         // Trading end hour (server time)
input bool             Skip_Friday_Late     = true;       // Skip Friday after end hour - 2h
input bool             Skip_Monday_Early    = true;       // Skip Monday before start hour + 2h

//==================== S/R LEVEL FILTER ===========================
input bool             Use_SR_Filter        = false;      // Enable S/R level filter
input ENUM_TIMEFRAMES  SR_TF                = PERIOD_H1;  // S/R detection timeframe
input int              SR_Lookback          = 200;        // S/R lookback bars
input double           SR_Zone_ATR          = 0.5;        // S/R zone width (x ATR)
input int              SR_Min_Touches       = 2;          // Min touches to confirm S/R level

//==================== RISK MANAGEMENT ============================
input double           SL_Percent           = 0.2;        // Stop Loss (% of open price)
input double           TP_Percent           = 0.4;        // Take Profit (% of open price)
input double           Risk_Money           = 5.0;        // Risk per trade (fixed money amount)

//==================== TRAILING STOP =============================
input bool             Use_Trailing         = true;       // Enable trailing stop
input double           Trail_Activate_Pct   = 0.15;       // Activate after profit reaches (% of price)
input double           Trail_Step_Pct       = 0.1;        // Trailing step (% of price)

//==================== GENERAL ==================================
input int              MagicNumber          = 222223;     // Magic Number (different from Gold build)
input int              Slippage             = 20;         // Slippage (points)
input bool             OnePositionPerSymbol = true;       // Only 1 open position per symbol
input int              Max_Trades_Per_Day   = 4;          // Max trades per day per symbol (0 = no limit)
input string           TradeComment         = "ATR_Breakout"; // Order comment

//==================== GLOBALS =================================
CTrade   trade;
int      atr_handle      = INVALID_HANDLE;
int      trend_handle    = INVALID_HANDLE;
int      htf_atr_handle  = INVALID_HANDLE;
datetime g_lastBarTime   = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   ConfigureFillingMode();

   atr_handle = iATR(_Symbol, Signal_TF, ATR_Period);
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

   if(Use_MTF_ATR)
   {
      htf_atr_handle = iATR(_Symbol, HTF, HTF_ATR_Period);
      if(htf_atr_handle == INVALID_HANDLE)
      {
         Print("Failed to create HTF ATR handle");
         return INIT_FAILED;
      }
   }

   g_lastBarTime = iTime(_Symbol, Signal_TF, 0);

   Print("=== ATR Candle Breakout EA (EUR build) Started ===");
   PrintFormat("Signal TF=%s ATR(%d)x%.2f | Trend=%s MTF_ATR=%s Time=%s S/R=%s | Risk=%.2f SL=%.2f%% TP=%.2f%%",
               EnumToString(Signal_TF), ATR_Period, ATR_Multiplier,
               Use_Trend_Filter ? "on" : "off", Use_MTF_ATR ? "on" : "off",
               Use_Time_Filter ? "on" : "off", Use_SR_Filter ? "on" : "off",
               Risk_Money, SL_Percent, TP_Percent);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(atr_handle     != INVALID_HANDLE) IndicatorRelease(atr_handle);
   if(trend_handle   != INVALID_HANDLE) IndicatorRelease(trend_handle);
   if(htf_atr_handle != INVALID_HANDLE) IndicatorRelease(htf_atr_handle);
   Print("=== ATR Candle Breakout EA (EUR build) Stopped ===");
}

//+------------------------------------------------------------------+
void OnTick()
{
   //--- Manage open trades every tick (trailing stop)
   ManageTrailingStops();

   //--- Only evaluate signals once per newly closed signal-TF candle
   if(!IsNewBar())
      return;

   int sig = CheckSignal();        // 1 = buy, -1 = sell, 0 = none
   if(sig == 0)
      return;

   bool isBuy = (sig == 1);

   //--- Apply optional filters
   if(!PassTimeFilter())        return;
   if(!PassTrendFilter(isBuy))  return;
   if(!PassMTFATR(isBuy))       return;
   if(!PassSRFilter(isBuy))     return;

   ExecuteTrade(isBuy);
}

//+------------------------------------------------------------------+
//| New-bar detection on the signal timeframe                        |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, Signal_TF, 0);
   if(t != g_lastBarTime)
   {
      g_lastBarTime = t;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Core signal: evaluate the just-closed candle (shift 1)           |
//+------------------------------------------------------------------+
int CheckSignal()
{
   double o = iOpen (_Symbol, Signal_TF, 1);
   double h = iHigh (_Symbol, Signal_TF, 1);
   double l = iLow  (_Symbol, Signal_TF, 1);
   double c = iClose(_Symbol, Signal_TF, 1);

   double range = h - l;
   if(range <= 0)
      return 0;

   double atr = GetBufferValue(atr_handle, 1);
   if(atr <= 0)
      return 0;

   //--- Candle must be larger than ATR x multiplier
   if(range < atr * ATR_Multiplier)
      return 0;

   //--- Body-to-range filter (skip long-wick candles)
   double body = MathAbs(c - o);
   if(Min_Body_Ratio_Pct > 0 && (body / range * 100.0) < Min_Body_Ratio_Pct)
      return 0;

   //--- Close must be near the relevant extreme
   double closeFromHigh = (h - c) / range * 100.0;  // for buy
   double closeFromLow  = (c - l) / range * 100.0;  // for sell

   bool bullish = (c > o);
   bool bearish = (c < o);

   if(bullish && closeFromHigh <= Close_Proximity_Pct) return 1;   // BUY
   if(bearish && closeFromLow  <= Close_Proximity_Pct) return -1;  // SELL

   return 0;
}

//+------------------------------------------------------------------+
//| Trend filter - trade only with the higher-TF MA direction        |
//+------------------------------------------------------------------+
bool PassTrendFilter(bool isBuy)
{
   if(!Use_Trend_Filter)
      return true;

   double ma = GetBufferValue(trend_handle, 1);
   if(ma <= 0)
      return true;   // cannot evaluate yet - do not block

   double c = iClose(_Symbol, Signal_TF, 1);
   return isBuy ? (c > ma) : (c < ma);
}

//+------------------------------------------------------------------+
//| MTF ATR confirmation - current HTF candle big and same direction |
//+------------------------------------------------------------------+
bool PassMTFATR(bool isBuy)
{
   if(!Use_MTF_ATR)
      return true;

   double o = iOpen (_Symbol, HTF, 0);
   double h = iHigh (_Symbol, HTF, 0);
   double l = iLow  (_Symbol, HTF, 0);
   double c = iClose(_Symbol, HTF, 0);

   double range = h - l;
   double atr   = GetBufferValue(htf_atr_handle, 0);
   if(atr <= 0 || range <= 0)
      return true;

   if(range < atr * HTF_ATR_Multiplier)
      return false;

   return isBuy ? (c > o) : (c < o);
}

//+------------------------------------------------------------------+
//| Time-of-day filter (server time)                                 |
//+------------------------------------------------------------------+
bool PassTimeFilter()
{
   if(!Use_Time_Filter)
      return true;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int hour = dt.hour;
   int dow  = dt.day_of_week;   // 0=Sun 1=Mon ... 5=Fri 6=Sat

   if(hour < Trade_Start_Hour || hour >= Trade_End_Hour)
      return false;

   if(Skip_Friday_Late  && dow == 5 && hour >= Trade_End_Hour - 2)
      return false;

   if(Skip_Monday_Early && dow == 1 && hour <  Trade_Start_Hour + 2)
      return false;

   return true;
}

//+------------------------------------------------------------------+
//| Support / Resistance filter                                      |
//|  Blocks buys into nearby resistance and sells into nearby support|
//+------------------------------------------------------------------+
bool PassSRFilter(bool isBuy)
{
   if(!Use_SR_Filter)
      return true;

   double atr = GetBufferValue(atr_handle, 1);
   if(atr <= 0)
      return true;

   double zone = atr * SR_Zone_ATR;
   double c    = iClose(_Symbol, Signal_TF, 1);

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, SR_TF, 0, SR_Lookback, rates);
   if(copied < 10)
      return true;

   //--- Collect swing highs (resistance) and swing lows (support)
   double swingHi[], swingLo[];
   int strength = 2;
   for(int i = strength; i < copied - strength; i++)
   {
      bool hi = true, lo = true;
      for(int j = 1; j <= strength; j++)
      {
         if(rates[i].high <= rates[i-j].high || rates[i].high <= rates[i+j].high) hi = false;
         if(rates[i].low  >= rates[i-j].low  || rates[i].low  >= rates[i+j].low ) lo = false;
      }
      if(hi) AppendDouble(swingHi, rates[i].high);
      if(lo) AppendDouble(swingLo, rates[i].low);
   }

   if(isBuy)
   {
      //--- Resistance just above the close blocks the buy
      for(int i = 0; i < ArraySize(swingHi); i++)
      {
         double level = swingHi[i];
         if(level < c)            continue;   // must be above price
         if(level - c > zone)     continue;   // must be near
         if(CountNear(swingHi, level, zone) >= SR_Min_Touches)
            return false;
      }
   }
   else
   {
      //--- Support just below the close blocks the sell
      for(int i = 0; i < ArraySize(swingLo); i++)
      {
         double level = swingLo[i];
         if(level > c)            continue;   // must be below price
         if(c - level > zone)     continue;
         if(CountNear(swingLo, level, zone) >= SR_Min_Touches)
            return false;
      }
   }
   return true;
}

//+------------------------------------------------------------------+
int CountNear(double &arr[], double level, double zone)
{
   int count = 0;
   for(int i = 0; i < ArraySize(arr); i++)
      if(MathAbs(arr[i] - level) <= zone)
         count++;
   return count;
}

//+------------------------------------------------------------------+
void AppendDouble(double &arr[], double val)
{
   int n = ArraySize(arr);
   ArrayResize(arr, n + 1);
   arr[n] = val;
}

//+------------------------------------------------------------------+
//| Open a trade                                                     |
//+------------------------------------------------------------------+
void ExecuteTrade(bool isBuy)
{
   if(!CanOpenNewPosition())
      return;

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double price  = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lots   = CalculateLotSize(price);

   double sl = 0, tp = 0;
   if(SL_Percent > 0)
      sl = isBuy ? price - price * SL_Percent / 100.0 : price + price * SL_Percent / 100.0;
   if(TP_Percent > 0)
      tp = isBuy ? price + price * TP_Percent / 100.0 : price - price * TP_Percent / 100.0;

   sl = (sl > 0) ? NormalizeDouble(sl, digits) : 0;
   tp = (tp > 0) ? NormalizeDouble(tp, digits) : 0;

   bool ok = isBuy ? trade.Buy (lots, _Symbol, price, sl, tp, TradeComment)
                   : trade.Sell(lots, _Symbol, price, sl, tp, TradeComment);

   if(ok)
      PrintFormat("%s opened @ %.5f SL=%.5f TP=%.5f lots=%.2f",
                  isBuy ? "BUY" : "SELL", price, sl, tp, lots);
   else
      PrintFormat("%s failed: %s", isBuy ? "BUY" : "SELL", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
//| Lot size from fixed-money risk and the SL distance               |
//+------------------------------------------------------------------+
double CalculateLotSize(double price)
{
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(SL_Percent <= 0 || tickValue <= 0 || tickSize <= 0)
      return minLot;

   double slDistPrice = price * SL_Percent / 100.0;
   double slTicks     = slDistPrice / tickSize;
   double riskPerLot  = slTicks * tickValue;
   if(riskPerLot <= 0)
      return minLot;

   double lots = Risk_Money / riskPerLot;

   lots = MathFloor(lots / stepLot) * stepLot;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Position guards                                                  |
//+------------------------------------------------------------------+
bool CanOpenNewPosition()
{
   if(CountMyPositions() > 0)
      return false;

   if(OnePositionPerSymbol && SymbolHasAnyPosition())
      return false;

   //--- Daily trade cap (counted from broker history => restart-safe)
   if(Max_Trades_Per_Day > 0 && CountTradesToday() >= Max_Trades_Per_Day)
      return false;

   return true;
}

//+------------------------------------------------------------------+
//| Count this EA's trades opened today (per symbol + magic).        |
//| Reads from deal history so an EA/terminal restart never loses    |
//| the count, and it resets automatically at the start of each day. |
//+------------------------------------------------------------------+
int CountTradesToday()
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
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;  // count only entries
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
bool SymbolHasAnyPosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Trailing stop                                                    |
//+------------------------------------------------------------------+
void ManageTrailingStops()
{
   if(!Use_Trailing || Trail_Activate_Pct <= 0)
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

      double price = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                                 : SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double profit_pct = (type == POSITION_TYPE_BUY)
                          ? (price - openPrice) / openPrice * 100.0
                          : (openPrice - price) / openPrice * 100.0;

      if(profit_pct < Trail_Activate_Pct)
         continue;

      double step    = price * Trail_Step_Pct / 100.0;
      double new_sl  = (type == POSITION_TYPE_BUY) ? price - step : price + step;
      new_sl = NormalizeDouble(new_sl, digits);

      if(type == POSITION_TYPE_BUY  && (curSL == 0 || new_sl > curSL))
         trade.PositionModify(ticket, new_sl, curTP);
      else if(type == POSITION_TYPE_SELL && (curSL == 0 || new_sl < curSL))
         trade.PositionModify(ticket, new_sl, curTP);
   }
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
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
