# Stock Monitor Mobile — Termux Prototype

手機優先的本機股票監控 Flask 網頁。Yahoo Finance / yfinance 為預設資料來源，SQLite 保存監控清單、模擬持倉、設定。

**目前僅為模擬／觀察工具，不會送出真實交易委託。** 自動模擬開關僅保存狀態，尚未連接自動下單或模擬交易引擎。

## 安裝
1. 安裝 Termux（建議 F-Droid 或官方 GitHub 發行版）。
2. 把 ZIP 解壓至手機可存取的資料夾；Termux 可先執行 `termux-setup-storage`。
3. 在 Termux `cd` 到專案資料夾。
4. 執行 `bash install_termux.sh`。
5. 執行 `bash start.sh`。
6. 手機瀏覽器開啟 **http://127.0.0.1:5000**。

## 功能
- 手機優先響應式介面；桌面也可使用。
- 台股／美股分頁、加入與移除監控股票。
- 台股代號自動補 `.TW`；上櫃可輸入 `.TWO`。
- Yahoo Finance 歷史資料（最大可取得範圍，以資料源為限）。
- 日／週／月 K 線切換、MA5/10/20/60、KD副圖；共享時間軸，價格與KD軸分開。
- 個股卡片收合；自動模擬開關（目前僅 UI/狀態）。
- 模擬持倉清單與手動新增。
- 設定頁與元大 API Key 欄位預留（尚未串接元大 API）。
- SQLite 保存資料，重新整理不會清除監控清單。

## 策略提醒
策略1–6目前是初步技術代理判斷，非完整逐字轉譯你的守則；加強條件7–9仍有代理／待計算項目。做多8已以近期高點突破做代理，做空8以近期低點跌破做代理，未實作KD/威廉背離、量能及吞噬實體棒的正式判斷；9尚待週線4T/12T交叉計算。請在策略定義確認前勿將分數當作交易依據。

## 常用指令
```bash
cd ~/StockMonitor_Termux_Mobile
bash install_termux.sh
bash start.sh
```

## 其他裝置連線
伺服器監聽 `0.0.0.0:5000`，同 Wi-Fi 下其他裝置可用手機 LAN IP 加 `:5000` 連入。不要把 5000 埠轉發到公網。API Key 目前以明文保存在 SQLite，建議暫勿填入真實密鑰。

## 背景運行
Android 可能限制背景程序。需要長時間運行時，請將 Termux 排除電池最佳化，並考慮 Termux:Boot / Wake Lock；仍不保證 24/7。
