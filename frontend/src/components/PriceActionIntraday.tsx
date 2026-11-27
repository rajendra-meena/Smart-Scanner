import {useEffect,useRef,useState} from "react";
import {Play,Square,Save,ShieldCheck} from "lucide-react";
import {priceActionApi,type PriceActionConfig,type PriceActionState} from "../lib/priceAction";

const initial:PriceActionConfig={enabled:true,auto_start:true,mode:"PAPER",max_positions:3,max_trades_per_day:5,stop_loss_points:15,risk_reward:2,max_spread_percent:1,volume_filter:false,volume_multiplier:1.2,exit_time:"15:15:00",max_daily_loss:1500,gainers_count:20,losers_count:20};
const fmt=(v:any)=>typeof v==="number"?v.toLocaleString("en-IN",{maximumFractionDigits:2}):"—";
const clock=(v:any)=>v?new Date(v).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—";

export default function PriceActionIntraday(){
 const [config,setConfig]=useState<PriceActionConfig>(initial),[state,setState]=useState<PriceActionState|null>(null);
 const settingsLoaded=useRef(false);
 const [tab,setTab]=useState<"dashboard"|"settings">("dashboard"),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
 const refresh=async()=>{try{const [s,st]=await Promise.all([priceActionApi.settings(),priceActionApi.status()]);if(!settingsLoaded.current){setConfig(s.config);settingsLoaded.current=true}setState(st)}catch(e){setError(e instanceof Error?e.message:"Unable to load Price Action strategy")}};
 useEffect(()=>{void refresh();const id=window.setInterval(()=>void refresh(),2000);return()=>window.clearInterval(id)},[]);
 const save=async()=>{setBusy(true);setError("");setNotice("");try{const r=await priceActionApi.save(config);setConfig(r.config);setNotice("Price Action settings saved.")}catch(e){setError(e instanceof Error?e.message:"Save failed")}finally{setBusy(false)}};
 const run=async(start:boolean)=>{setBusy(true);setError("");setNotice("");try{setState(start?await priceActionApi.start():await priceActionApi.stop());setNotice(start?"Price Action PAPER strategy started.":"Price Action strategy stopped.")}catch(e){setError(e instanceof Error?e.message:"Action failed")}finally{setBusy(false)}};
 const exitPosition=async(id:string)=>{if(!window.confirm("Close this PAPER position at the latest available Zerodha bid price?"))return;setBusy(true);setError("");setNotice("");try{setState(await priceActionApi.manualExit(id));setNotice("Manual exit completed using the latest Zerodha bid quote.")}catch(e){setError(e instanceof Error?e.message:"Manual exit failed")}finally{setBusy(false)}};
 const field=(label:string,key:keyof PriceActionConfig,step="1",min="0")=><label className="pa-field">{label}<input type="number" min={min} step={step} value={config[key] as number} onChange={e=>setConfig({...config,[key]:Number(e.target.value)})}/></label>;
 return <section className="panel pa-workspace">
  <div className="panel-title"><div><h2>Price Action Intraday</h2><p>Market structure · Supply &amp; demand · Liquidity sweeps · Candle confirmation · F&amp;O options</p></div><span className="live-label">PAPER ONLY</span></div>
  <div className="pa-statusline"><span className={state?.running?"pa-dot active":"pa-dot"}/><b>{state?.running?"STRATEGY RUNNING":"STRATEGY STOPPED"}</b><span>·</span><span>{state?.market_data_ready?"ZERODHA FEED HEALTHY":"WAITING FOR FRESH ZERODHA TICKS"}</span><span>·</span><span>F&O ATM CE/PE · PAPER</span></div>
  <nav className="tabs"><button className={tab==="dashboard"?"tab active":"tab"} onClick={()=>setTab("dashboard")}>Dashboard</button><button className={tab==="settings"?"tab active":"tab"} onClick={()=>setTab("settings")}>Strategy settings</button></nav>
  {error&&<div className="alert">{error}</div>}{notice&&<div className="strategy-notice">{notice}</div>}
  {tab==="dashboard"&&<>
   <div className="pa-controls"><button className="primary" disabled={busy||!!state?.running} onClick={()=>void run(true)}><Play size={14}/> Start Paper Strategy</button><button className="secondary-action" disabled={busy||!state?.running} onClick={()=>void run(false)}><Square size={14}/> Stop Strategy</button><span>Auto-start: {config.enabled&&config.auto_start?"ON":"OFF"}</span></div>
   <div className="orders-overview-stats"><div><small>OPEN POSITIONS</small><strong>{state?.open_positions??0}</strong></div><div><small>TRADES TODAY</small><strong>{state?.trades_count??0}</strong></div><div><small>WATCHLIST</small><strong>{state?.watchlist?.length??0}</strong></div><div><small>MODE</small><strong>PAPER</strong></div></div>
   <div className="pa-section-head"><div><h3>Live market structure</h3><p>40 configured F&O movers are monitored. Signal evaluation begins after 8 completed one-minute candles per stock.</p></div><span className="pa-live-caption">LIVE ZERODHA DATA</span></div>
   {state?.watchlist?.length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STOCK</th><th>STRUCTURE</th><th>SWING HIGH</th><th>SWING LOW</th><th>DEMAND ZONE</th><th>SUPPLY ZONE</th><th>STATUS / REASON</th></tr></thead><tbody>{state.watchlist.map(r=><tr key={r.symbol}><td><b>{r.symbol}</b></td><td>{r.structure}</td><td>{fmt(r.swing_high)}</td><td>{fmt(r.swing_low)}</td><td>{r.demand_zone?fmt(r.demand_zone.low)+" – "+fmt(r.demand_zone.high):"—"}</td><td>{r.supply_zone?fmt(r.supply_zone.low)+" – "+fmt(r.supply_zone.high):"—"}</td><td>{r.signal?<b className={r.signal==="CE"?"up":"down"}>{r.signal} SIGNAL</b>:r.reason}</td></tr>)}</tbody></table></div>:<div className="empty">Waiting for completed one-minute candles. At least 8 candles are required before structure analysis.</div>}
   <div className="pa-section-head"><div><h3>Open PAPER positions</h3><p>Option bid-side mark-to-market and automatic SL / target checks</p></div></div>
   {state?.positions?.filter(p=>p.status==="OPEN").length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STOCK / CONTRACT</th><th>TYPE</th><th>ENTRY TIME</th><th>ENTRY</th><th>BID / LTP</th><th>STOP LOSS</th><th>TARGET</th><th>QTY</th><th>P&amp;L</th><th>QUOTE</th><th>ACTION</th></tr></thead><tbody>{state.positions.filter(p=>p.status==="OPEN").map(p=><tr key={p.id}><td>{p.symbol}<small>{p.option_symbol}</small></td><td>{p.direction}</td><td>{clock(p.opened_at)}</td><td>₹{fmt(p.entry_price)}</td><td>₹{fmt(p.bid)} / {fmt(p.ltp)}</td><td>₹{fmt(p.stop_price)}</td><td>₹{fmt(p.target_price)}</td><td>{p.quantity}</td><td>₹{fmt(p.pnl)}</td><td>{p.quote_stale?<b className="down">STALE — WAITING</b>:<b className="up">LIVE</b>}</td><td><button className="secondary-action" disabled={busy||!!p.quote_stale} onClick={()=>void exitPosition(p.id)}>Exit Trade</button></td></tr>)}</tbody></table></div>:<div className="empty">No open Price Action positions.</div>}
   <div className="pa-section-head"><div><h3>Completed trades</h3><p>Saved in the dedicated Price Action SQLite trade database</p></div></div>
   {state?.trades?.length?<div className="table-scroll"><table className="trade-table"><thead><tr><th>STOCK / CONTRACT</th><th>DIRECTION</th><th>ENTRY TIME</th><th>EXIT TIME</th><th>ENTRY</th><th>EXIT</th><th>STOP LOSS</th><th>TARGET</th><th>QTY</th><th>P&amp;L</th><th>EXIT REASON</th></tr></thead><tbody>{state.trades.map((p:any)=><tr key={p.id}><td>{p.symbol}<small>{p.option_symbol}</small></td><td>{p.direction}</td><td>{clock(p.opened_at)}</td><td>{clock(p.closed_at)}</td><td>₹{fmt(p.entry_price)}</td><td>₹{fmt(p.exit_price)}</td><td>₹{fmt(p.stop_price)}</td><td>₹{fmt(p.target_price)}</td><td>{p.quantity}</td><td>₹{fmt(p.pnl)}</td><td>{p.exit_reason}</td></tr>)}</tbody></table></div>:<div className="empty">No completed Price Action trades today.</div>}
   <div className="pa-section-head"><h3>Strategy events</h3></div>
   <div className="pa-events">{(state?.events||[]).slice().reverse().slice(0,12).map((e,i)=><div key={i}><span>{clock(e.time)}</span><b>{e.event}</b><small>{e.symbol||e.detail||e.reason||e.option||""}</small></div>)}</div>
  </>}
  {tab==="settings"&&<>
   <div className="strategy-safety-note"><ShieldCheck size={17}/><div><b>Paper execution only</b><p>Live broker order placement is not implemented. Paper entries use actual subscribed Zerodha option quotes, buy-side ask for entry and bid-side exits.</p></div></div>
   <div className="global-defaults-grid">
    <label className="pa-toggle">Strategy enabled<select value={String(config.enabled)} onChange={e=>setConfig({...config,enabled:e.target.value==="true"})}><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
    <label className="pa-toggle">Automatic start<select value={String(config.auto_start)} onChange={e=>setConfig({...config,auto_start:e.target.value==="true"})}><option value="true">ON — after 09:20 IST</option><option value="false">OFF — manual start</option></select></label>
    {field("F&O top gainers","gainers_count","1","1")}\n    {field("F&O top losers","losers_count","1","1")}\n    {field("Maximum open positions","max_positions","1","1")}
    {field("Maximum trades per day","max_trades_per_day","1","1")}
    {field("Stop loss (option premium points)","stop_loss_points","0.05","0.05")}
    {field("Risk : Reward (e.g. 2 = 1:2)","risk_reward","0.1","1")}
    {field("Maximum bid/ask spread (%)","max_spread_percent","0.05","0.05")}
    {field("Maximum daily loss (₹)","max_daily_loss","100","1")}
    {field("Volume multiplier","volume_multiplier","0.1","1")}
    <label className="pa-toggle">Volume confirmation<select value={String(config.volume_filter)} onChange={e=>setConfig({...config,volume_filter:e.target.value==="true"})}><option value="false">OFF — optional filter</option><option value="true">ON — require volume expansion</option></select></label>
    <label className="pa-toggle">Square-off time (IST)<input type="time" step="1" value={config.exit_time} onChange={e=>setConfig({...config,exit_time:e.target.value})}/></label>
   </div>
   <p className="pa-settings-footnote">Signal universe uses F&amp;O stocks from Zerodha's instrument master. Structure is built from one-minute underlying equity ticks. Option contract selection uses the nearest available expiry and ATM strike.</p>
   <button className="primary" disabled={busy||!!state?.running} onClick={()=>void save()}><Save size={14}/> Save strategy settings</button>
  </>}
 </section>;
}
