import React, {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import Dashboard from './dashboard';
import './shared/app/globals.css';
const allowed=['overview','deals','compare','suppliers','sources','jev'] as const;
type View=typeof allowed[number];
function App(){
  const read=():View=>{const hash=window.location.hash.slice(1);return allowed.includes(hash as View)?hash as View:'deals'};
  const [view,setView]=useState<View>(read);
  useEffect(()=>{const listener=()=>setView(read());window.addEventListener('hashchange',listener);return()=>window.removeEventListener('hashchange',listener)},[]);
  return <Dashboard view={view}/>;
}
createRoot(document.getElementById('root')!).render(<App/>);
