import {useEffect,useState} from "react";
import {api,type BreakoutStatus,type BreakoutTrade} from "../lib/openingBreakout";
export default function OpeningBreakout(){
 const [status,setStatus]=useState<BreakoutStatus|null>(null),[trades,setTrades]=useState<BreakoutTrade[]>([]);
 const [mode,setMode]=useState<"PAPER"|"LIVE">("PAPER"),[error,setError]=useState(""),[busy,setBusy]=useState(false);
 const refresh=async()=>{try{setStatus(await api.status());setTrades(await api.trades())}catch(e){setError(e instanceof Error?e.message:"Unable to load strategy")}};
 useEffect(()=>{void refresh();const id=window.setInterval(()=>void refresh(),1500);return()=>window.clearInterval(id)},[]);
 const start=async()=>{setBusy(true);setError("");try{await api.start(mode);await refresh()}catch(e){setError(e instanceof Error?e.message:"Start failed")}finally{setBusy(false)}};
 const stop=async()=>{setBusy(true);try{await api.stop();await refresh()}catch(e){setError(e instanceof Error?e.message:"Stop failed")}finally{setBusy(false)}};
 return <section className="panel opening-breakout"><div className="panel-title"><div><h2>Opening Price Breakout</h2><p>F&O top movers · ATM options · 09:15–09:30 IST</p></div><span className="live-label">{status?.running?"RUNNING":"STOPPED"}</span></div>
 <div className="breakout-toolbar"><div className="toggle"><button className={mode==="PAPER"?"chosen":""} onClick={()=>setMode("PAPER")}>PAPER</button><button className={mode==="LIVE"?"chosen":""} onClick={()=>setMode("LIVE")}>LIVE</button></div><span>15-point SL · 1 lot · max 6 positions</span></div>
 {error&&<div className="alert">{error}</div>}<div className="breakout-actions"><button className="primary" disabled={busy||!!status?.running||!status?.market_data_ready||mode==="LIVE"} onClick={start}>{busy?"Working…":mode==="LIVE"?"Live locked":"Start Paper Strategy"}</button><button className="subtle-button" disabled={busy||!status?.running} onClick={stop}>Stop</button></div>
 {mode==="LIVE"&&<div className="info-panel"><div><b>Live mode is safety-locked</b><p>Real option streaming, protective stop handling, partial-fill recovery and broker reconciliation must pass verification before automatic orders are allowed.</p></div></div>}
 <div className="breakout-stats"><div><small>WATCHLIST</small><strong>{status?.watchlist_count??0}/6</strong></div><div><small>OPEN POSITIONS</small><strong>{status?.open_positions??0}/6</strong></div><div><small>TRADES TODAY</small><strong>{status?.trades_count??0}</strong></div><div><small>FEED</small><strong>{status?.market_data_ready?"READY":"WAITING"}</strong></div></div>
 <div className="section-head"><h3>Execution Log</h3><p>Strategy status and trade history</p></div><div className="breakout-trades">{trades.length?trades.map(t=><div className="breakout-trade" key={t.id}><b>{t.symbol}</b><span>{t.direction} · {t.mode}</span><strong>{t.status}</strong></div>):<div className="empty">No strategy trades yet.</div>}</div>
 </section>
}
