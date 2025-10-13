from datetime import datetime


def initialize(context):
    context.watchlist = ['RELIANCE', 'TCS']


def on_tick(context, tick_batch):
    # Very simple demo: buy 1 RELIANCE on first tick
    md = context.get_market_data('RELIANCE')
    ltp = md.get('ltp') if md else None
    if not ltp:
        return
    # Place a paper/live order using context
    context.place_order('RELIANCE', quantity=1, side='BUY')
