from flask import Flask, jsonify, request, send_from_directory
import sqlite3, os
from datetime import datetime
APP_DIR=os.path.dirname(os.path.abspath(__file__)); DB=os.path.join(APP_DIR,'stock_monitor.db')
app=Flask(__name__,static_folder='static')
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS watchlist(ticker TEXT PRIMARY KEY, market TEXT NOT NULL, added_at TEXT NOT NULL, auto_sim INTEGER DEFAULT 0, collapsed INTEGER DEFAULT 0)')
        c.execute('CREATE TABLE IF NOT EXISTS simulated_positions(id INTEGER PRIMARY KEY AUTOINCREMENT,ticker TEXT,market TEXT,side TEXT,qty REAL,entry_price REAL,opened_at TEXT,status TEXT DEFAULT "OPEN",closed_at TEXT,exit_price REAL,note TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('yuanta_api_key','')")
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('refresh_seconds','60')")
init_db()
def normalize(t,m):
    t=t.strip().upper()
    if m=='TW' and not (t.endswith('.TW') or t.endswith('.TWO')): t += '.TW'
    return t
def get_hist(ticker, interval='1d', period='max'):
    import yfinance as yf
    data=yf.Ticker(ticker).history(period=period,interval=interval,auto_adjust=False)
    if data is None or data.empty: return None
    data=data.reset_index(); tc='Datetime' if 'Datetime' in data.columns else 'Date'
    data.rename(columns={tc:'Date'},inplace=True); data['Date']=data['Date'].astype(str)
    return data.dropna(subset=['Open','High','Low','Close'])
def kd(df,n=9):
    lo=df.Low.rolling(n).min(); hi=df.High.rolling(n).max(); r=((df.Close-lo)/(hi-lo)*100).replace([float('inf'),-float('inf')],float('nan')).fillna(50)
    return r.ewm(alpha=1/3,adjust=False).mean(),r.ewm(alpha=1/3,adjust=False).mean().ewm(alpha=1/3,adjust=False).mean()
def slope_angle(series,lookback=5):
    import math
    if len(series)<lookback or series.iloc[-lookback:].isna().any(): return None
    a=float(series.iloc[-lookback]); b=float(series.iloc[-1]); return round(math.degrees(math.atan((b-a)/abs(a)*100)),1) if a else None
def scores(df):
    if len(df)<65:return {'long':0,'short':0,'boost_long':0,'boost_short':0,'long_conditions':[],'short_conditions':[],'note':'歷史資料不足，日線資料至少需要約65根。'}
    c=df.Close; m5=c.rolling(5).mean();m10=c.rolling(10).mean();m20=c.rolling(20).mean();m60=c.rolling(60).mean();k,d=kd(df); ang=slope_angle(m20)
    L=[(c.iloc[-1]>m20.iloc[-1],'收盤位於日20T上方'),(m5.iloc[-1]>m10.iloc[-1]>m20.iloc[-1],'5T/10T/20T多頭排列'),(m20.iloc[-1]>m20.iloc[-5],'日20T上升'),(c.iloc[-1]>m60.iloc[-1],'收盤位於日60T上方'),(k.iloc[-1]>d.iloc[-1] and k.iloc[-2]<=d.iloc[-2],'KD黃金交叉'),(ang is not None and ang>40,f'日20T上升斜率角度>40°（估算{ang}°）')]
    S=[(c.iloc[-1]<m20.iloc[-1],'收盤位於日20T下方'),(m5.iloc[-1]<m10.iloc[-1]<m20.iloc[-1],'5T/10T/20T空頭排列'),(m20.iloc[-1]<m20.iloc[-5],'日20T下降'),(c.iloc[-1]<m60.iloc[-1],'收盤位於日60T下方'),(k.iloc[-1]<d.iloc[-1] and k.iloc[-2]>=d.iloc[-2],'KD死亡交叉'),(ang is not None and ang<-40,f'日20T下降斜率角度<-40°（估算{ang}°）')]
    BL=[(m5.iloc[-1]>m20.iloc[-1],'5T位於20T上方'),(c.iloc[-1]>=df.High.rolling(20).max().iloc[-2],'近20根高點突破代理（A8未完整判讀）'),(False,'週4T上穿週12T（尚待週線計算）')]
    BS=[(m5.iloc[-1]<m20.iloc[-1],'5T位於20T下方'),(c.iloc[-1]<=df.Low.rolling(20).min().iloc[-2],'近20根低點跌破代理（B8未完整判讀）'),(False,'週4T下穿週12T（尚待週線計算）')]
    pack=lambda arr:[{'ok':bool(x),'label':s} for x,s in arr]
    return {'long':sum(x for x,_ in L),'short':sum(x for x,_ in S),'boost_long':sum(x for x,_ in BL),'boost_short':sum(x for x,_ in BS),'long_conditions':pack(L),'short_conditions':pack(S),'boost_long_conditions':pack(BL),'boost_short_conditions':pack(BS),'angle20':ang,'note':'策略條件1–6為初版技術代理判斷，尚需依你的原始守則逐項校正。'}
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
    if field not in ('auto_sim','collapsed'):return jsonify({'error':'不允許欄位'}),400
    with db() as c:c.execute(f'UPDATE watchlist SET {field}=? WHERE ticker=?',(int(bool(p.get('value'))),ticker))
    return jsonify({'ok':True})
@app.get('/api/quote/<ticker>')
def quote(ticker):
    interval=request.args.get('interval','1d'); period='max'
    try:
        df=get_hist(ticker,interval,period)
        if df is None:return jsonify({'error':'查無行情。請檢查代號或 Yahoo Finance。'}),404
        for n in (5,10,20,60):df[f'MA{n}']=df.Close.rolling(n).mean()
        df['K'],df['D']=kd(df); last=df.iloc[-1]
        daily=df if interval=='1d' else get_hist(ticker,'1d','max')
        st=scores(daily) if daily is not None else scores(df)
        name=ticker
        try:
            import yfinance as yf; name=yf.Ticker(ticker).info.get('shortName') or ticker
        except Exception:pass
        prev=float(df.Close.iloc[-2]) if len(df)>1 else float(last.Close)
        payload={'ticker':ticker,'name':name,'latest':float(last.Close),'change':float(last.Close-prev),'change_pct':float((last.Close/prev-1)*100) if prev else 0,'date':str(last.Date),'strategy':st,'data':[]}
        for row in df.tail(1000).to_dict('records'):
            out={k:(float(v) if k!='Date' and v==v else None) for k,v in row.items()};out['Date']=str(row['Date']);payload['data'].append(out)
        return jsonify(payload)
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
