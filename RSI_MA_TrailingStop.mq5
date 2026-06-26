//+------------------------------------------------------------------+
//|                                          RSI_MA_TrailingStop.mq5 |
//|                          RSI + Moving Average Filter Expert      |
//|                                    MT5 (MQL5) Only               |
//+------------------------------------------------------------------+
#property copyright "Claude AI"
#property link      "https://www.mql5.com"
#property version   "1.02"

#include <Trade\Trade.mqh>

//--- Input Parameters
input int              RSI_Period           = 9;         // RSI Period
input ENUM_TIMEFRAMES  RSI_Timeframe        = PERIOD_M15;// RSI Timeframe
input double           RSI_Buy_Threshold    = 35;        // RSI Buy Threshold (< this = buy signal)
input double           RSI_Sell_Threshold   = 65;        // RSI Sell Threshold (> this = sell signal)

input bool             MA_Filter_On         = true;      // Enable Moving Average Filter
input int              MA_Period            = 50;        // MA Period
input ENUM_TIMEFRAMES  MA_Timeframe         = PERIOD_H4; // MA Timeframe
input ENUM_MA_METHOD   MA_Type              = MODE_SMA;  // MA Type

input double           SL_Percent           = 1.0;       // Stop Loss % of open price (0 = no SL)
input double           TP_Percent           = 0.75;      // Take Profit % of open price (0 = no TP)

input double           TSL_Trigger_Percent  = 0.3;       // Trailing Stop Trigger % of profit (0 = off)
input double           TSL_Distance_Percent = 0.2;       // Trailing Stop Distance %
input double           TSL_Step_Percent     = 0.05;      // Trailing Stop Step %

input bool             Risk_Type_IsPercent  = true;      // Risk Type: true=% of balance, false=fixed money
input double           Risk_Value           = 1.0;       // Risk: % of balance or fixed money amount

input int              MagicNumber          = 123456;    // Magic Number for this EA
input string           TradeComment         = "RSI_MA";  // Order Comment

//--- Global Variables
CTrade trade;
int    rsi_handle = INVALID_HANDLE;
int    ma_handle  = INVALID_HANDLE;

double last_rsi      = 50.0;   // Previous RSI value, for cross detection
bool   g_buy_locked  = false;  // Blocks new BUY signals until RSI crosses back above 50
bool   g_sell_locked = false;  // Blocks new SELL signals until RSI crosses back below 50

string g_buyLockVar, g_sellLockVar;   // Names of persisted terminal global variables

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   ConfigureFillingMode();

   rsi_handle = iRSI(_Symbol, RSI_Timeframe, RSI_Period, PRICE_CLOSE);
   if(rsi_handle == INVALID_HANDLE)
   {
      Print("Failed to create RSI handle");
      return INIT_FAILED;
   }

   if(MA_Filter_On)
   {
      ma_handle = iMA(_Symbol, MA_Timeframe, MA_Period, 0, MA_Type, PRICE_CLOSE);
      if(ma_handle == INVALID_HANDLE)
      {
         Print("Failed to create MA handle");
         return INIT_FAILED;
      }
   }

   //--- Restore lockout state across restarts (per-symbol, per-magic so EAs never collide)
   g_buyLockVar  = StringFormat("RSI_MA_%d_%s_BuyLock",  MagicNumber, _Symbol);
   g_sellLockVar = StringFormat("RSI_MA_%d_%s_SellLock", MagicNumber, _Symbol);
   g_buy_locked  = GlobalVariableCheck(g_buyLockVar)  ? (GlobalVariableGet(g_buyLockVar)  != 0) : false;
   g_sell_locked = GlobalVariableCheck(g_sellLockVar) ? (GlobalVariableGet(g_sellLockVar) != 0) : false;

   double rsi_buffer[];
   ArraySetAsSeries(rsi_buffer, true);
   if(CopyBuffer(rsi_handle, 0, 0, 1, rsi_buffer) > 0)
      last_rsi = rsi_buffer[0];

   Print("=== RSI + MA EA Started ===");
   PrintFormat("RSI: period=%d tf=%s | MA: on=%s period=%d tf=%s | BuyLocked=%s SellLocked=%s",
               RSI_Period, EnumToString(RSI_Timeframe),
               MA_Filter_On ? "yes" : "no", MA_Period, EnumToString(MA_Timeframe),
               g_buy_locked ? "yes" : "no", g_sell_locked ? "yes" : "no");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(rsi_handle != INVALID_HANDLE)
      IndicatorRelease(rsi_handle);
   if(ma_handle != INVALID_HANDLE)
      IndicatorRelease(ma_handle);
   Comment("");
   Print("=== RSI + MA EA Stopped ===");
}

//+------------------------------------------------------------------+
void OnTick()
{
   double rsi = GetRSI();
   if(rsi < 0) return;

   //--- Release lockouts once RSI has crossed back through the midline
   if(g_buy_locked  && rsi > 50) SetBuyLocked(false);
   if(g_sell_locked && rsi < 50) SetSellLocked(false);

   bool buy_signal  = (last_rsi >= RSI_Buy_Threshold  && rsi < RSI_Buy_Threshold);
   bool sell_signal = (last_rsi <= RSI_Sell_Threshold && rsi > RSI_Sell_Threshold);

   if(buy_signal  && g_buy_locked)  buy_signal  = false;
   if(sell_signal && g_sell_locked) sell_signal = false;

   if(buy_signal && MA_Filter_On)
   {
      double ma  = GetMA();
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      if(ma > 0 && ask < ma) buy_signal = false;
   }

   if(sell_signal && MA_Filter_On)
   {
      double ma  = GetMA();
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(ma > 0 && bid > ma) sell_signal = false;
   }

   if(buy_signal)
   {
      ExecuteBuy();
      SetBuyLocked(true);
   }
   if(sell_signal)
   {
      ExecuteSell();
      SetSellLocked(true);
   }

   ManageTrailingStops();

   last_rsi = rsi;
}

//+------------------------------------------------------------------+
double GetRSI()
{
   double rsi_buffer[];
   ArraySetAsSeries(rsi_buffer, true);
   if(CopyBuffer(rsi_handle, 0, 0, 1, rsi_buffer) < 1)
      return -1;
   return rsi_buffer[0];
}

//+------------------------------------------------------------------+
double GetMA()
{
   if(!MA_Filter_On || ma_handle == INVALID_HANDLE)
      return 0;
   double ma_buffer[];
   ArraySetAsSeries(ma_buffer, true);
   if(CopyBuffer(ma_handle, 0, 0, 1, ma_buffer) < 1)
      return 0;
   return ma_buffer[0];
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
void ExecuteBuy()
{
   if(HasPosition(POSITION_TYPE_BUY))
      return;

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double lots   = CalculateLotSize(ask, SL_Percent);

   double sl = (SL_Percent > 0) ? NormalizeDouble(ask - (ask * SL_Percent / 100.0), digits) : 0;
   double tp = (TP_Percent > 0) ? NormalizeDouble(ask + (ask * TP_Percent / 100.0), digits) : 0;

   if(trade.Buy(lots, _Symbol, ask, sl, tp, TradeComment))
      PrintFormat("BUY opened @ %.5f SL=%.5f TP=%.5f lots=%.2f", ask, sl, tp, lots);
   else
      PrintFormat("BUY failed: %s", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
void ExecuteSell()
{
   if(HasPosition(POSITION_TYPE_SELL))
      return;

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lots   = CalculateLotSize(bid, SL_Percent);

   double sl = (SL_Percent > 0) ? NormalizeDouble(bid + (bid * SL_Percent / 100.0), digits) : 0;
   double tp = (TP_Percent > 0) ? NormalizeDouble(bid - (bid * TP_Percent / 100.0), digits) : 0;

   if(trade.Sell(lots, _Symbol, bid, sl, tp, TradeComment))
      PrintFormat("SELL opened @ %.5f SL=%.5f TP=%.5f lots=%.2f", bid, sl, tp, lots);
   else
      PrintFormat("SELL failed: %s", trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
bool HasPosition(ENUM_POSITION_TYPE type)
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(PositionGetInteger(POSITION_TYPE) == type) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
double CalculateLotSize(double price, double sl_percent)
{
   double risk_money = 0;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   if(Risk_Type_IsPercent)
      risk_money = balance * Risk_Value / 100.0;
   else
      risk_money = Risk_Value;

   if(sl_percent <= 0)
      risk_money = 0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double sl_dist   = (price * sl_percent / 100.0) / tickSize;

   if(sl_dist <= 0 || tickValue <= 0)
      return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   double lots = risk_money / (sl_dist * tickValue);

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   lots = MathFloor(lots / stepLot) * stepLot;
   lots = MathMax(minLot, MathMin(maxLot, lots));

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
void ManageTrailingStops()
{
   if(TSL_Trigger_Percent <= 0)
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
      double profit_pct = ((price - openPrice) / openPrice) * 100.0;
      if(type == POSITION_TYPE_SELL)
         profit_pct = ((openPrice - price) / openPrice) * 100.0;

      if(profit_pct >= TSL_Trigger_Percent)
      {
         double tsl_dist = openPrice * TSL_Distance_Percent / 100.0;
         double new_sl   = (type == POSITION_TYPE_BUY) ? price - tsl_dist : price + tsl_dist;
         new_sl = NormalizeDouble(new_sl, digits);

         double step_dist = openPrice * TSL_Step_Percent / 100.0;
         if(type == POSITION_TYPE_BUY  && new_sl > curSL + step_dist)
            trade.PositionModify(ticket, new_sl, curTP);
         else if(type == POSITION_TYPE_SELL && new_sl < curSL - step_dist)
            trade.PositionModify(ticket, new_sl, curTP);
      }
   }
}

//+------------------------------------------------------------------+
void SetBuyLocked(bool locked)
{
   g_buy_locked = locked;
   GlobalVariableSet(g_buyLockVar, locked ? 1.0 : 0.0);
}

//+------------------------------------------------------------------+
void SetSellLocked(bool locked)
{
   g_sell_locked = locked;
   GlobalVariableSet(g_sellLockVar, locked ? 1.0 : 0.0);
}
