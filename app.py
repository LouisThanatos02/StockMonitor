from flask import Flask, jsonify, request, send_from_directory
import sqlite3, os
from datetime import datetime
from market_data import aggregate_daily_bars, display_time, exchange_for_ticker, remove_incomplete_hourly_bars, volume_breakout_a8
APP_DIR=os.path.dirname(os.path.abspath(__file__)); DB=os.path.join(APP_DIR,'stock_monitor.db')
app=Flask(__name__,static_folder='static')

def json_safe(obj):
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, 'item'):
        try:
            return obj.item()
        except Exception:
            pass
    return obj

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS watchlist(ticker TEXT PRIMARY KEY, market TEXT NOT NULL, added_at TEXT NOT NULL, auto_sim INTEGER DEFAULT 0, collapsed INTEGER DEFAULT 0, auto_long INTEGER DEFAULT 0, auto_short INTEGER DEFAULT 0)')
        cols=[r['name'] for r in c.execute('PRAGMA table_info(watchlist)').fetchall()]
        for col in ('auto_long','auto_short'):
            if col not in cols: c.execute(f'ALTER TABLE watchlist ADD COLUMN {col} INTEGER DEFAULT 0')
        c.execute('CREATE TABLE IF NOT EXISTS simulated_positions(id INTEGER PRIMARY KEY AUTOINCREMENT,ticker TEXT,market TEXT,side TEXT,qty REAL,entry_price REAL,opened_at TEXT,status TEXT DEFAULT "OPEN",closed_at TEXT,exit_price REAL,note TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('yuanta_api_key','')")
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('refresh_seconds','60')")
init_db()
def normalize(t,m):
    t=t.strip().upper()
    if m=='TW' and not (t.endswith('.TW') or t.endswith('.TWO')): t += '.TW'
    return t
def get_hist(ticker, interval='1d', period='max', include_session_schedule=False):
    import yfinance as yf
    yf_ticker=yf.Ticker(ticker)
    data=yf_ticker.history(period=period,interval=interval,auto_adjust=False,prepost=False)
    session_closes={}
    if data is None or data.empty:
        return (None,session_closes) if include_session_schedule else None
    if include_session_schedule:
        try:
            periods=yf_ticker.history_metadata.get('tradingPeriods')
            if periods is not None and hasattr(periods,'iterrows'):
                for session_date, period_row in periods.iterrows():
                    end=period_row.get('end')
                    if end is not None and not getattr(end,'isnat',False):
                        session_closes[session_date.date()]=end.to_pydatetime().timetz().replace(tzinfo=None)
        except Exception:
            # Without an official session schedule, the bar filter fails safe.
            session_closes={}
    data=data.reset_index(); tc='Datetime' if 'Datetime' in data.columns else 'Date'
    data.rename(columns={tc:'Date'},inplace=True)
    data=data.dropna(subset=['Open','High','Low','Close']).sort_values('Date').reset_index(drop=True)
    return (data,session_closes) if include_session_schedule else data
def kd(df,n=9):
    lo=df.Low.rolling(n).min(); hi=df.High.rolling(n).max(); spread=hi-lo
    r=((df.Close-lo)/spread*100).replace([float('inf'),-float('inf')],float('nan')).fillna(50)
    return r.ewm(alpha=1/3,adjust=False).mean(),r.ewm(alpha=1/3,adjust=False).mean().ewm(alpha=1/3,adjust=False).mean()
def slope_angle(series,lookback=5):
    import math
    if len(series)<lookback or series.iloc[-lookback:].isna().any(): return None
    a=float(series.iloc[-lookback]); b=float(series.iloc[-1]); return round(math.degrees(math.atan((b-a)/abs(a)*100)),1) if a else None
def _condition(value, label):
    return {'ok': bool(value) if value is not None else None,
            'status': 'unknown' if value is None else ('pass' if value else 'fail'), 'label': label}

def _structure_state(df):
    """Return confirmed higher-high/higher-low and lower-high/lower-low states."""
    highs=df.High.tolist(); lows=df.Low.tolist(); pivots_high=[]; pivots_low=[]
    for i in range(2, len(df)-2):
        if highs[i] > max(highs[i-2:i]) and highs[i] >= max(highs[i+1:i+3]): pivots_high.append((i, highs[i]))
        if lows[i] < min(lows[i-2:i]) and lows[i] <= min(lows[i+1:i+3]): pivots_low.append((i, lows[i]))
    if len(pivots_high)<2 or len(pivots_low)<2: return None, None
    return (pivots_high[-1][1] > pivots_high[-2][1] and pivots_low[-1][1] > pivots_low[-2][1],
            pivots_high[-1][1] < pivots_high[-2][1] and pivots_low[-1][1] < pivots_low[-2][1])

def scores(df):
    c=df.Close; high=df.High; low=df.Low; volume=df.Volume if 'Volume' in df else None
    m4=c.rolling(4).mean(); m5=c.rolling(5).mean(); m10=c.rolling(10).mean(); m12=c.rolling(12).mean()
    m20=c.rolling(20).mean(); m60=c.rolling(60).mean(); k,d=kd(df); ang=slope_angle(m20)
    higher,lower=_structure_state(df)
    ready=lambda series,index=-1:len(series)>=abs(index) and series.iloc[index]==series.iloc[index]
    def compare(a,b,op):
        if a is None or b is None or a!=a or b!=b:return None
        return op(a,b)
    kd_ready=len(df)>=9
    long_cross=compare(k.iloc[-1],d.iloc[-1],lambda a,b:a>b) if kd_ready and ready(k) and ready(d) and ready(k,-2) and ready(d,-2) else None
    long_cross=(long_cross and k.iloc[-2]<=d.iloc[-2]) if long_cross is not None else None
    short_cross=compare(k.iloc[-1],d.iloc[-1],lambda a,b:a<b) if kd_ready and ready(k) and ready(d) and ready(k,-2) and ready(d,-2) else None
    short_cross=(short_cross and k.iloc[-2]>=d.iloc[-2]) if short_cross is not None else None
    # Keep the established six base rules, but evaluate them on the selected
    # timeframe. None means history is insufficient and must not score either way.
    L=[_condition(compare(c.iloc[-1],m20.iloc[-1],lambda a,b:a>b) if ready(m20) else None,'收盤位於20T上方'),
       _condition((m5.iloc[-1]>m10.iloc[-1]>m20.iloc[-1]) if ready(m5) and ready(m10) and ready(m20) else None,'5T/10T/20T多頭排列'),
       _condition((m20.iloc[-1]>m20.iloc[-5]) if ready(m20,-5) else None,'20T上升'),
       _condition(compare(c.iloc[-1],m60.iloc[-1],lambda a,b:a>b) if ready(m60) else None,'收盤位於60T上方'),
       _condition(long_cross,'KD黃金交叉'),
       _condition((ang>40) if ang is not None else None,f'20T上升斜率角度 > 40°（估算 {ang}°）')]
    S=[_condition(compare(c.iloc[-1],m20.iloc[-1],lambda a,b:a<b) if ready(m20) else None,'收盤位於20T下方'),
       _condition((m5.iloc[-1]<m10.iloc[-1]<m20.iloc[-1]) if ready(m5) and ready(m10) and ready(m20) else None,'5T/10T/20T空頭排列'),
       _condition((m20.iloc[-1]<m20.iloc[-5]) if ready(m20,-5) else None,'20T下降'),
       _condition(compare(c.iloc[-1],m60.iloc[-1],lambda a,b:a<b) if ready(m60) else None,'收盤位於60T下方'),
       _condition(short_cross,'KD死亡交叉'),
       _condition((ang<-40) if ang is not None else None,f'20T下降斜率角度 < -40°（估算 {ang}°）')]
    prev_high=high.rolling(20).max().iloc[-2] if len(df)>=21 else None
    prev_low=low.rolling(20).min().iloc[-2] if len(df)>=21 else None
    volume_a8=volume_breakout_a8(volume.tolist()) if volume is not None else None
    BL=[_condition((m5.iloc[-1]>m20.iloc[-1]) if ready(m5) and ready(m20) else None,'5T位於20T上方'),
        _condition((c.iloc[-1]>=prev_high) if prev_high is not None and prev_high==prev_high else None,'近20根高點突破'),
        _condition(volume_a8,'A8：當期成交量 > 前六根均量 × 1.5')]
    BS=[_condition((m5.iloc[-1]<m20.iloc[-1]) if ready(m5) and ready(m20) else None,'5T位於20T下方'),
        _condition((c.iloc[-1]<=prev_low) if prev_low is not None and prev_low==prev_low else None,'近20根低點跌破'),
        _condition(volume_a8,'A8：當期成交量 > 前六根均量 × 1.5')]

    cross_up=ready(m4,-2) and ready(m12,-2) and m4.iloc[-1]>m12.iloc[-1] and m4.iloc[-2]<=m12.iloc[-2]
    cross_down=ready(m4,-2) and ready(m12,-2) and m4.iloc[-1]<m12.iloc[-1] and m4.iloc[-2]>=m12.iloc[-2]
    structural=[_condition(higher,'高高低高結構（已確認波段高低點）'),_condition(lower,'低高低低結構（已確認波段高低點）')]
    interval_signals=[_condition((m4.iloc[-1]>m12.iloc[-1]) if ready(m4) and ready(m12) else None,'4T位於12T上方'),
                      _condition(cross_up if ready(m4,-2) and ready(m12,-2) else None,'4T上穿12T'),
                      _condition(cross_down if ready(m4,-2) and ready(m12,-2) else None,'4T下穿12T'),
                      _condition((k.iloc[-1]>d.iloc[-1]) if kd_ready and ready(k) and ready(d) else None,'KD持續正分（K>D）'),
                      _condition((k.iloc[-1]<d.iloc[-1]) if kd_ready and ready(k) and ready(d) else None,'KD持續負分（K<D）')]
    if len(df)>=20 and kd_ready and ready(k,-20):
        prior_low_i=min(range(len(df)-20,len(df)-5),key=lambda i:low.iloc[i])
        recent_low_i=min(range(len(df)-5,len(df)),key=lambda i:low.iloc[i])
        prior_high_i=max(range(len(df)-20,len(df)-5),key=lambda i:high.iloc[i])
        recent_high_i=max(range(len(df)-5,len(df)),key=lambda i:high.iloc[i])
        bullish_div=low.iloc[recent_low_i]<low.iloc[prior_low_i] and k.iloc[recent_low_i]>k.iloc[prior_low_i]
        bearish_div=high.iloc[recent_high_i]>high.iloc[prior_high_i] and k.iloc[recent_high_i]<k.iloc[prior_high_i]
        divergence=[_condition(bullish_div,'KD看漲背離（近5根低點 vs 前15根）'),_condition(bearish_div,'KD看跌背離（近5根高點 vs 前15根）')]
    else: divergence=[_condition(None,'KD看漲背離（至少需要20根 K 線）'),_condition(None,'KD看跌背離（至少需要20根 K 線）')]
    # Supplementary pattern readings are surfaced as diagnostics, not silently
    # folded into the established score without a project rule defining their weight.
    if len(df)>=2:
        prev,cur=df.iloc[-2],df.iloc[-1]
        bullish_engulf=cur.Close>cur.Open and prev.Close<prev.Open and cur.Open<=prev.Close and cur.Close>=prev.Open
        bearish_engulf=cur.Close<cur.Open and prev.Close>prev.Open and cur.Open>=prev.Close and cur.Close<=prev.Open
        patterns=[_condition(bullish_engulf,'看漲吞噬型態'),_condition(bearish_engulf,'看跌吞噬型態')]
    else: patterns=[_condition(None,'看漲吞噬型態'),_condition(None,'看跌吞噬型態')]
    unknown=sum(item['status']=='unknown' for item in L+S+BL+BS+structural+interval_signals+divergence+patterns)
    return {'long':sum(item['ok'] is True for item in L),'short':sum(item['ok'] is True for item in S),
            'boost_long':sum(item['ok'] is True for item in BL),'boost_short':sum(item['ok'] is True for item in BS),
            'long_conditions':L,'short_conditions':S,'boost_long_conditions':BL,'boost_short_conditions':BS,
            'structure_conditions':structural,'interval_conditions':interval_signals,'divergence_conditions':divergence,'pattern_conditions':patterns,
            'angle20':ang,'note':f'{unknown} 項策略/型態條件因歷史資料不足而無法判定。' if unknown else '策略分數與補充訊號均以目前選取週期的 K 線計算。'}
@app.get('/')
def home(): return send_from_directory('static','index.html')
@app.get('/api/watchlist')
def watchlist():
    with db() as c: rows=c.execute('SELECT * FROM watchlist ORDER BY market,ticker').fetchall()
    return jsonify([dict(r) for r in rows])
@app.post('/api/watchlist')
def add():
    p=request.get_json(force=True); m=p.get('market','TW'); t=normalize(p.get('ticker',''),m)
    if not t:return jsonify({'error':'請輸入代號'}),400
    with db() as c:c.execute('INSERT OR IGNORE INTO watchlist(ticker,market,added_at) VALUES(?,?,?)',(t,m,datetime.now().isoformat(timespec='seconds')))
    return jsonify({'ok':True,'ticker':t})
@app.delete('/api/watchlist/<ticker>')
def remove(ticker):
    with db() as c:c.execute('DELETE FROM watchlist WHERE ticker=?',(ticker,))
    return jsonify({'ok':True})
@app.post('/api/watchlist/<ticker>/toggle')
def toggle(ticker):
    p=request.get_json(force=True); field=p.get('field')
    if field not in ('auto_long','auto_short','collapsed'):return jsonify({'error':'不允許欄位'}),400
    with db() as c:c.execute(f'UPDATE watchlist SET {field}=? WHERE ticker=?',(int(bool(p.get('value'))),ticker))
    return jsonify({'ok':True})
@app.get('/api/quote/<ticker>')
def quote(ticker):
    interval=request.args.get('interval','1d')
    if interval not in ('1d','1wk','1mo','60m'):
        return jsonify({'error':'不支援的行情週期'}),400
    try:
        market=exchange_for_ticker(ticker)
        if interval in ('1wk','1mo'):
            daily,session_closes=get_hist(ticker,'1d','max',include_session_schedule=True)
            raw=[] if daily is None else daily.to_dict('records')
            grouped=aggregate_daily_bars(raw,interval,ticker,session_closes=session_closes)
            import pandas as pd
            df=pd.DataFrame(grouped)
            status='使用已完成週線；本週未完成 K 線已排除' if interval=='1wk' else '使用已完成月線；本月未完成 K 線已排除'
        elif interval=='60m':
            hourly,session_closes=get_hist(ticker,'60m','60d',include_session_schedule=True)
            raw=[] if hourly is None else hourly.to_dict('records')
            filtered=remove_incomplete_hourly_bars(raw,ticker,session_closes=session_closes)
            import pandas as pd
            df=pd.DataFrame(filtered)
            status='Yahoo Finance 60 分鐘資料；僅提供最近 60 天，未完成 K 線已排除'
            if df.empty:
                return jsonify({'error':f'Yahoo Finance 目前沒有可用的 {ticker} 60 分鐘 K 線（資料範圍最多約 60 天）。'}),422
        else:
            df=get_hist(ticker,'1d','max')
            status='Yahoo Finance 日線資料'
        if df is None or df.empty:
            return jsonify({'error':f'Yahoo Finance 目前沒有 {ticker} 的 {interval} 行情資料。'}),404
        df=df.dropna(subset=['Open','High','Low','Close']).sort_values('Date').reset_index(drop=True)
        df['K'],df['D']=kd(df); last=df.iloc[-1]
        st=scores(df)
        name=ticker
        try:
            import yfinance as yf; name=yf.Ticker(ticker).info.get('shortName') or ticker
        except Exception:pass
        prev=float(df.Close.iloc[-2]) if len(df)>1 else float(last.Close)
        payload={'ticker':ticker,'name':name,'interval':interval,'market':market,'period_status':status,
                 'history_limit':'最近 60 天' if interval=='60m' else '可用完整歷史',
                 'latest':float(last.Close),'change':float(last.Close-prev),'change_pct':float((last.Close/prev-1)*100) if prev else 0,
                 'date':display_time(last.Date),'strategy':st,'data':[]}
        for row in df.tail(1000).to_dict('records'):
            out={k:(float(v) if k!='Date' and v==v else None) for k,v in row.items()};out['Date']=display_time(row['Date']);payload['data'].append(out)
        return jsonify(json_safe(payload))
    except Exception as e:return jsonify({'error':str(e)}),502
@app.get('/api/positions')
def get_positions():
    with db() as c:rows=c.execute('SELECT * FROM simulated_positions ORDER BY id DESC').fetchall()
    return jsonify([dict(r) for r in rows])
@app.post('/api/positions')
def create_position():
    p=request.get_json(force=True)
    try:
        with db() as c:c.execute('INSERT INTO simulated_positions(ticker,market,side,qty,entry_price,opened_at,note) VALUES(?,?,?,?,?,?,?)',(p['ticker'].strip().upper(),p['market'],p['side'],float(p['qty']),float(p['entry_price']),datetime.now().isoformat(timespec='seconds'),p.get('note','')))
        return jsonify({'ok':True})
    except Exception as e:return jsonify({'error':str(e)}),400
@app.get('/api/settings')
def get_settings():
    with db() as c:rows=c.execute('SELECT key,value FROM settings').fetchall()
    return jsonify({r['key']:r['value'] for r in rows})
@app.post('/api/settings')
def set_settings():
    p=request.get_json(force=True)
    with db() as c:
        for k,v in p.items():
            if k in ('yuanta_api_key','refresh_seconds'):c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
    return jsonify({'ok':True})
if __name__=='__main__':
    print('Stock Monitor Mobile server running: http://127.0.0.1:5000')
    app.run(host='0.0.0.0',port=5000,debug=False,threaded=True)
