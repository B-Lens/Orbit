import pandas as pd
from orbit.core.exception_manager import ExceptionManager

eM = ExceptionManager()

def find_last_swing(df, point=None, n=3):
    """
    Find the last swing high and swing low in a DataFrame starting from a given point.
    If point is None, start from the end of the DataFrame.
    Args:
        df (DataFrame): OHLC DataFrame with 'High' and 'Low' columns.
        point (int, optional): Index to start looking back from.
        n (int): Number of candles before and after to confirm a swing.
    Returns:
        dict: Last swing high and swing low index and values.
    """
    if point is None:
        point = len(df) - 1  # start from the last index

    last_swing_high = None
    last_swing_low = None

    for i in range(point, n-1, -1):
        high = df['high'].iloc[i]
        highs_before = df['high'].iloc[i-n:i]
        highs_after = df['high'].iloc[i+1:i+n+1] if i+n+1 <= len(df) else df['high'].iloc[i+1:]

        if all(high > highs_before) and all(high > highs_after):
            last_swing_high = {'index': i, 'value': high}
            break

    for i in range(point, n-1, -1):
        low = df['low'].iloc[i]
        lows_before = df['low'].iloc[i-n:i]
        lows_after = df['low'].iloc[i+1:i+n+1] if i+n+1 <= len(df) else df['low'].iloc[i+1:]

        if all(low < lows_before) and all(low < lows_after):
            last_swing_low = {'index': i, 'value': low}
            break

    return {
        'last_swing_high': last_swing_high,
        'last_swing_low': last_swing_low
    }

def get_swing_sl(df:pd.DataFrame, n = 3, **kwargs):

    """Get the swing stop loss based on the last swing high or low.
    Args:
        df (pd.DataFrame): DataFrame containing 'high' and 'low' columns.
        n (int): Number of candles before and after to confirm a swing.
        **kwargs: Optional parameters to specify buy or sell price.
            - buy_price (float): Price at which to buy.
            - sell_price (float): Price at which to sell.
    Returns:
        float: The calculated stop loss price.
    Raises:
        ValueError: If neither buy_price nor sell_price is provided, or if both are provided.
    """
    try:
        if not isinstance(df, pd.DataFrame):
            raise ValueError("Input df must be a pandas DataFrame.")
        
        buy_price = kwargs.get('buy_price', None)
        sell_price = kwargs.get('sell_price', None)
        if buy_price is not None and sell_price is not None:
            raise ValueError("Only one of buy_price or sell_price should be provided.")
        
        if buy_price is not None:
            last_swing = find_last_swing(df, point=None, n=n)
            if last_swing['last_swing_low'] is None:
                raise ValueError("No swing high found in the DataFrame.")
            
            sl = last_swing['last_swing_low']['value']
            if sl >= buy_price:
                raise ValueError("Swing low stop loss is greater than or equal to the buy price.")
            if sl <=0:
                raise ValueError("Swing low stop loss is negative or Zero, which is not valid.")
        
            return sl
        
        elif sell_price is not None:
            last_swing = find_last_swing(df, point=None, n=n)
            if last_swing['last_swing_high'] is None:
                raise ValueError("No swing low found in the DataFrame.")
            sl = last_swing['last_swing_high']['value']

            if sl <= sell_price:
                raise ValueError("Swing high stop loss is less than or equal to the sell price.")
            if sl <= 0:
                raise ValueError("Swing high stop loss is negative or zero, which is not valid.")
            return sl
        
        else:
            raise ValueError("Either buy_price or sell_price must be provided.")
    except Exception as e:
        eM.handle_exception(e, "Error in get_swing_sl")
        return None

   