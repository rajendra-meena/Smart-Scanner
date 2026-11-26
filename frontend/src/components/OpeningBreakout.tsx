import {useEffect,useState} from "react";
import {api,type BreakoutStatus,type BreakoutTrade,type BreakoutSettings,type BreakoutConfig} from "../lib/openingBreakout";

const money=(v:unknown)=>typeof v==="number"?new Intl.NumberFormat("en-IN",{maximumFractionDigits:2}).format(v):"—";
const time=(v:unknown)=>typeof v==="string"&&v?new Date(v).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—";

export default function OpeningBreakout(){
 const [status,setStatus]=useState<BreakoutStatus|null>(null),[trades,setTrades]=useState<BreakoutTrade[]>([]);
 const [settings,setSettings]=useState<BreakoutSettings|null>(null),[draft,setDraft]=useState<BreakoutConfig|null>(null);
 const [view,setView]=useState<"dashboard"|"settings">("dashboard");
 const [error,setError]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false);
 const refresh=async()=>{try{const [s,t,c]=await Promise.all([api.status(),api.trades(),api.settings()]);setStatus(s);setTrades(t);setSettings(c);setDraft(c.config)}catch(e){setError(e instanceof Error?e.message:"Unable to load strategy data")}};
 useEffect(()=>{void refresh();const id=window.setInterval(()=>void refresh(),2000);return()=>window.clearInterval(id)},[]);
 const updateMode=async(mode:"PAPER"|"LIVE")=>{setBusy(true);setError("");setNotice("");try{const result=await api.setMode(mode);setSettings(result);setNotice(mode==="PAPER"?"Paper mode selected and saved.":"Live mode preference saved. Live order execution is not available until broker execution safeguards are implemented.");}catch(e){setError(e instanceof Error?e.message:"Unable to update mode")}finally{setBusy(false)}};
 const save=async()=>{if(!draft)return;setBusy(true);setError("");setNotice("");try{const result=await api.saveSettings(draft);setSettings(result);setDraft(result.config);setNotice("Settings saved successfully.");await refresh()}catch(e){setError(e instanceof Error?e.message:"Unable to save settings")}finally{setBusy(false)}};
 const start=async()=>{setBusy(true);setError("");try{await api.start(settings?.mode||"PAPER");await refresh()}catch(e){setError(e instanceof Error?e.message:"Start failed")}finally{setBusy(false)}};
 const stop=async()=>{setBusy(true);setError("");try{await api.stop();await refresh()}catch(e){setError(e instanceof Error?e.message:"Stop failed")}finally{setBusy(false)}};
 const positions=status?.positions||[],events=status?.events||[];
 const setField=<K extends keyof BreakoutConfig,>(key:K,value:BreakoutConfig[K])=>setDraft(prev=>prev?{...prev,[key]:value}:prev);
 return <section className="panel opening-breakout">
  <div className="panel-title"><div><h2>Opening Price Breakout</h2><p>Live Zerodha market feed · F&O movers · ATM options</p></div><span className={status?.running?"live-label":"live-label"}>{status?.running?"RUNNING":"STOPPED"}</span></div>
  <div className="tabs"><button className={view==="dashboard"?"tab active":"tab"} onClick={()=>setView("dashboard")}>Dashboard</button><button className={view==="settings"?"tab active":"tab"} onClick={()=>setView("settings")}>Strategy settings</button></div>
  {error&&<div className="alert">{error}</div>}{notice&&<div className="status-strip"><b>{notice}</b></div>}
  {view==="settings"?<div className="strategy-settings">
   <div className="section-head"><h3>Execution mode</h3><p>Paper is the default. Your selection is saved on the server.</p></div>
   <div className="breakout-toolbar"><div className="toggle"><button className={settings?.mode==="PAPER"?"chosen":""} disabled={busy||!!status?.running} onClick={()=>void updateMode("PAPER")}>PAPER</button><button className={settings?.mode==="LIVE"?"chosen":""} disabled={busy||!!status?.running} onClick={()=>void updateMode("LIVE")}>LIVE</button></div><b>{settings?.mode||"PAPER"} MODE</b></div>
   {settings?.mode==="LIVE"&&<div className="info-panel"><div><b>Live execution is not yet available</b><p>{settings.live_block_reason} The selected preference is saved, but starting the strategy in Live mode will be rejected. No live order is sent.</p></div></div>}
   {draft&&<><div className="section-head"><h3>Strategy parameters</h3><p>These values are applied by the Opening Breakout strategy.</p></div>
    <div className="settings-grid">
     <label>Top gainers count<input type="number" min="1" max="20" value={draft.gainers_count} onChange={e=>setField("gainers_count",Number(e.target.value))}/></label>
     <label>Top losers count<input type="number" min="1" max="20" value={draft.losers_count} onChange={e=>setField("losers_count",Number(e.target.value))}/></label>
     <label>Stop loss (premium points)<input type="number" min="0.05" max="100" step="0.05" value={draft.stop_loss_points} onChange={e=>setField("stop_loss_points",Number(e.target.value))}/></label>
     <label>Maximum open positions<input type="number" min="1" max="6" value={draft.max_positions} onChange={e=>setField("max_positions",Number(e.target.value))}/></label>
     <label>Maximum bid/ask spread (%)<input type="number" min="0.01" max="10" step="0.05" value={draft.max_spread_percent} onChange={e=>setField("max_spread_percent",Number(e.target.value))}/></label>
    </div><p className="settings-note">Exit time is currently fixed at 09:30 IST in the strategy engine, so it is not presented as an editable setting. Target is not configured by this strategy.</p>
    <button className="primary" disabled={busy} onClick={()=>void save()}>{busy?"Saving…":"Save settings"}</button>
   </>}
  </div>:<>
   <div className="breakout-toolbar"><div className="toggle"><button className={settings?.mode==="PAPER"?"chosen":""} onClick={()=>void updateMode("PAPER")} disabled={busy||!!status?.running}>PAPER</button><button className={settings?.mode==="LIVE"?"chosen":""} onClick={()=>void updateMode("LIVE")} disabled={busy||!!status?.running}>LIVE</button></div><span>Execution preference: {settings?.mode||"PAPER"}</span></div>
   <div className="breakout-actions"><button className="primary" disabled={busy||!!status?.running||!status?.market_data_ready||settings?.mode!=="PAPER"} onClick={()=>void start()}>{busy?"Working…":settings?.mode==="LIVE"?"Live execution unavailable":"Start Paper Strategy"}</button><button className="subtle-button" disabled={busy||!status?.running} onClick={()=>void stop()}>Stop Strategy</button><button className="subtle-button" disabled={busy} onClick={()=>void refresh()}>Refresh</button></div>
   <div className="breakout-stats"><div><small>WATCHLIST</small><strong>{status?.watchlist_count??0}</strong></div><div><small>OPEN / PENDING</small><strong>{status?.open_positions??0}</strong></div><div><small>TRADES TODAY</small><strong>{status?.trades_count??0}</strong></div><div><small>MARKET FEED</small><strong>{status?.market_data_ready?"READY":"WAITING"}</strong></div></div>
   <div className="section-head"><h3>Selected F&O Stocks</h3><p>Live gainers and losers selected when strategy starts</p></div>
   <div className="breakout-watchlist">{status?.watchlist?.length?status.watchlist.map(s=><div className="breakout-watch" key={s.symbol}><b>{s.symbol}</b><span>Open ₹{money(s.opening_price)}</span><span>LTP ₹{money(s.last_price)}</span><strong>{s.signal|| (s.traded?"TRADED":s.skipped?"SKIPPED":"SCANNING")}</strong></div>):<div className="empty">No strategy watchlist loaded. Start Paper mode during the permitted market window.</div>}</div>
   <div className="section-head"><h3>Realtime positions</h3><p>Position status and P&amp;L from strategy execution data</p></div>
   {positions.length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STOCK / CONTRACT</th><th>SIDE</th><th>ENTRY TIME</th><th>ENTRY</th><th>LTP</th><th>STOP LOSS</th><th>QTY</th><th>P&amp;L</th><th>STATUS</th></tr></thead><tbody>{positions.map((p:any)=><tr key={p.id}><td><b>{p.symbol}</b><small>{p.option_symbol||"Option quote pending"}</small></td><td>{p.direction||"—"}</td><td>{time(p.opened_at)}</td><td>₹{money(p.entry_price)}</td><td>₹{money(p.ltp)}</td><td>₹{money(p.stop_price)}</td><td>{p.quantity??"—"}</td><td className={typeof p.pnl==="number"?(p.pnl>=0?"up":"down"):""}>{typeof p.pnl==="number"?"₹"+money(p.pnl):"—"}</td><td>{p.status}</td></tr>)}</tbody></table></div>:<div className="empty">No active or pending positions.</div>}
   <div className="section-head"><h3>Completed trades</h3><p>Saved strategy trade history</p></div>
   {trades.length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STOCK</th><th>MODE</th><th>ENTRY TIME</th><th>EXIT TIME</th><th>ENTRY</th><th>EXIT</th><th>QTY</th><th>P&amp;L</th><th>EXIT REASON</th></tr></thead><tbody>{trades.map(t=><tr key={t.id}><td><b>{t.symbol}</b><small>{t.option_symbol||t.direction}</small></td><td>{t.mode}</td><td>{time(t.opened_at)}</td><td>{time(t.closed_at)}</td><td>₹{money(t.entry_price)}</td><td>₹{money(t.exit_price)}</td><td>{t.quantity??"—"}</td><td className={typeof t.pnl==="number"?(t.pnl>=0?"up":"down"):""}>{typeof t.pnl==="number"?"₹"+money(t.pnl):"—"}</td><td>{t.exit_reason||"—"}</td></tr>)}</tbody></table></div>:<div className="empty">No completed trades are stored yet.</div>}
   <div className="section-head"><h3>Execution events</h3><p>Strategy signals, option subscriptions and simulated fills</p></div>
   <div className="breakout-trades">{events.length?events.slice().reverse().map((e:any,i:number)=><div className="breakout-trade" key={i}><b>{e.symbol||"STRATEGY"}</b><span>{e.event} {e.direction||""}</span><strong>{e.status||e.reason||"INFO"}</strong><small>{e.detail||e.time}</small></div>):<div className="empty">No execution events recorded.</div>}</div>
  </>}
 </section>;
}
