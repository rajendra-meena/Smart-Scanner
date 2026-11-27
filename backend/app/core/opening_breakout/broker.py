"""Official Kite Connect adapter; never used by Paper mode."""
from kiteconnect import KiteConnect

class KiteBroker:
    def __init__(self, api_key: str, access_token: str):
        self.client = KiteConnect(api_key=api_key)
        self.client.set_access_token(access_token)

    def instruments(self, exchange: str = "NFO"):
        return self.client.instruments(exchange)

    def quote(self, instruments: list[str]):
        return self.client.quote(instruments)

    def place_market(self, symbol: str, quantity: int, transaction_type: str, tag: str):
        return self.client.place_order(
            variety=self.client.VARIETY_REGULAR, exchange=self.client.EXCHANGE_NFO,
            tradingsymbol=symbol, transaction_type=transaction_type, quantity=quantity,
            product=self.client.PRODUCT_MIS, order_type=self.client.ORDER_TYPE_MARKET,
            validity=self.client.VALIDITY_DAY, market_protection=-1, tag=tag[:20])

    def orders(self): return self.client.orders()
    def order_history(self, order_id: str): return self.client.order_history(order_id)
    def positions(self): return self.client.positions()
