import {useEffect,useState} from "react";
import {api,type BreakoutStatus,type BreakoutTrade} from "../lib/openingBreakout";
export default function OpeningBreakout(){
 const [status,setStatus]=useState<BreakoutStatus|null>(null),[trades,setTrades]=useState<BreakoutTrade[]>([]);
 const [mode,setMode]=useState<"PAPER"|"LIVE">("PAPER"),[error,setError]=useState(""),[busy,setBusy]=useState(false);
 const refresh=async()=>{try{setStatus(await api.status());setTrades(await api.trades())}catch(e){setError(e instanceof Error?e.message:"Unable to load strategy")}};
 useEffect(()=>{void refresh();const id=window.setInterval(()=>void refresh(),1500);return()=>window.clearInterval(id)},[]);
 const start=async()=>{setBusy(true);setError("");try{await api.start(mode);await refresh()}catch(e){setError(e instanceof Error?e.message:"Start failed")}finally{setBusy(false)}};
 const stop=async()=>{setBusy(true);try{await api.stop();await refresh()}catch(e){setError(e instanceof Error?e.message:"Stop failed")}finally{setBusy(false)}};
 const watchlist=status?.watchlist||[],positions=status?.positions||[],events=status?.events||[];
 return <section className="panel opening-breakout">
  <div className="panel-title"><div><h2>Opening Price Breakout</h2><p>Top 3 F&O gainers + top 3 losers · ATM options · 09:15–09:30 IST</p></div><span className="live-label">{status?.running?"RUNNING":"STOPPED"}</span></div>
  <div className="breakout-toolbar"><div className="toggle"><button className={mode==="PAPER"?"chosen":""} onClick={()=>setMode("PAPER")}>PAPER</button><button className={mode==="LIVE"?"chosen":""} onClick={()=>setMode("LIVE")}>LIVE</button></div><span>15-point premium SL · nearest ATM · 1 lot · max 6 positions</span></div>
  {error&&<div className="alert">{error}</div>}
  <div className="breakout-actions"><button className="primary" disabled={busy||!!status?.running||!status?.market_data_ready||mode==="LIVE"} onClick={start}>{busy?"Working…":mode==="LIVE"?"Live locked":"Start Paper Strategy"}</button><button className="subtle-button" disabled={busy||!status?.running} onClick={stop}>Stop Strategy</button></div>
  {mode==="LIVE"&&<div className="info-panel"><div><b>Live mode is safety-locked</b><p>Automatic live orders stay disabled until protective SL orders, partial fills, rejected orders and broker square-off reconciliation are verified end to end. Paper mode does not place broker orders.</p></div></div>}
  <div className="breakout-stats"><div><small>WATCHLIST</small><strong>{status?.watchlist_count??0}/6</strong></div><div><small>OPEN / PENDING</small><strong>{status?.open_positions??0}/6</strong></div><div><small>TRADES TODAY</small><strong>{status?.trades_count??0}</strong></div><div><small>MARKET FEED</small><strong>{status?.market_data_ready?"READY":"WAITING"}</strong></div></div>
  <div className="section-head"><h3>Selected F&O Stocks</h3><p>Fixed daily selection from live F&O equity universe</p></div>
  <div className="breakout-watchlist">{watchlist.length?watchlist.map((s:any)=><div className="breakout-watch" key={s.symbol}><b>{s.symbol}</b><span>Open ₹{s.opening_price??"—"}</span><span>Price ₹{s.last_price??"—"}</span><strong>{s.signal|| (s.traded?"TRADED":s.skipped?"SKIPPED":"SCANNING")}</strong></div>):<div className="empty">Start Paper mode during market hours to load the F&O watchlist.</div>}</div>
  <div className="section-head"><h3>Positions</h3><p>Independent position-level 15-point stop loss and 09:30 time exit</p></div>
  <div className="breakout-trades">{positions.length?positions.map((p:any)=><div className="breakout-trade" key={p.id}><b>{p.symbol}</b><span>{p.direction} · {p.option_symbol}</span><strong>{p.status}</strong><small>{p.entry_price? "Entry ₹"+p.entry_price:"Awaiting option quote"}{p.ltp?" · LTP ₹"+p.ltp:""}{p.stop_price?" · SL ₹"+p.stop_price:""}</small></div>):<div className="empty">No option positions yet.</div>}</div>
  <div className="section-head"><h3>Execution Events</h3><p>Signals, contract subscription, simulated fills and exits</p></div>
  <div className="breakout-trades">{events.length?events.slice().reverse().map((e:any,i:number)=><div className="breakout-trade" key={i}><b>{e.symbol||"STRATEGY"}</b><span>{e.event} {e.direction||""}</span><strong>{e.status||e.reason||"INFO"}</strong><small>{e.detail||e.time}</small></div>):<div className="empty">No events recorded.</div>}</div>
  <div className="section-head"><h3>Completed Trades</h3></div>
  <div className="breakout-trades">{trades.length?trades.map(t=><div className="breakout-trade" key={t.id}><b>{t.symbol}</b><span>{t.direction} · {t.mode}</span><strong>{t.status}</strong><small>{t.entry_price?"Entry ₹"+t.entry_price:""}{t.exit_price?" → Exit ₹"+t.exit_price:""}{t.exit_reason?" · "+t.exit_reason:""}</small></div>):<div className="empty">No completed strategy trades yet.</div>}</div>
 </section>
}
