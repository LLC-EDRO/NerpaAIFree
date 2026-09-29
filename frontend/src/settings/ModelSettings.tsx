import {useEffect,useState} from 'react';
import './ModelSettings.css';
type Settings={provider:'ollama';baseUrl:string;textModel:string;visionModel:string;imageModel:string;keyConfigured:boolean};
type Model={id:string;name:string};
async function request<T>(path:string,body?:unknown,method='POST'):Promise<T>{
 const response=await fetch('/api/settings/models'+path,body===undefined?undefined:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw Error(data.error?.message || 'Не удалось выполнить запрос');return data;
}
export function ModelSettings({onBack,onSaved}:{onBack:()=>void;onSaved:()=>void}){
 const [settings,setSettings]=useState<Settings>();
 const [models,setModels]=useState<Model[]>([]),[pending,setPending]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let live=true;request<Settings>('').then(s=>{if(live)setSettings(s);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
 const change=(patch:Partial<Settings>)=>{setSettings(s=>s?{...s,...patch}:s);setMessage('');};
 async function run(action:'discover'|'save'){
  if(!settings)return;setPending(true);setMessage('');setError('');
  try{
   const body=settings;
   if(action==='discover'){
    const result=await request<{models:Model[]}>('/discover',body);setModels(result.models);setMessage(`Доступно моделей: ${result.models.length}. Выберите модель и сохраните настройки.`);
   }else{
    const saved=await request<Settings>('',body,'PUT');setSettings(saved);setMessage('Сохранено. Выбор применяется к новым экспериментам.');onSaved();
   }
  }catch(e){setError(e instanceof Error?e.message:'Ошибка подключения');}finally{setPending(false);}
 }
 function options(selected:string){return <>{selected && !models.some(m=>m.id===selected) && <option value={selected}>{selected}</option>}{models.map(m=><option key={m.id} value={m.id}>{m.name===m.id?m.id:`${m.name} (${m.id})`}</option>)}</>;}
 return <section className="model-settings workspace" aria-labelledby="model-settings-title">
  <button className="settings-back" onClick={onBack}>← К экспериментам</button>
  <div className="page-heading"><div className="eyebrow">КОНФИГУРАЦИЯ ГЕНЕРАЦИИ</div><h1 id="model-settings-title">Настройки моделей</h1><p>Выберите модели для новых презентаций. Уже созданные эксперименты сохраняют прежний выбор.</p></div>
  {error && <p className="settings-error" role="alert">{error}</p>}
  {!settings ? <p>Загрузка настроек…</p> : <form className="settings-card" onSubmit={e=>{e.preventDefault();void run('save');}}>
   <fieldset disabled={pending}>
    <p className="settings-note">Локальный Ollama · Gemma 4 для текста и анализа слайдов.</p>
    <p className="settings-note">Текстовая модель составляет план, заполняет поля и проверяет содержание. Визуальная модель анализирует слайды и помогает исправлять расположение объектов.</p>
     <label>Адрес Ollama<input type="url" required value={settings.baseUrl} onChange={e=>{change({baseUrl:e.target.value,textModel:'',visionModel:''});setModels([]);}} placeholder="http://127.0.0.1:11434" /></label>
     <button type="button" onClick={()=>void run('discover')}>Загрузить доступные модели</button>
     <label>Модель для текста<select required value={settings.textModel} onChange={e=>change({textModel:e.target.value})}><option value="">Выберите модель из списка</option>{options(settings.textModel)}</select></label>
     <label>Модель для анализа изображений слайдов<select required value={settings.visionModel} onChange={e=>change({visionModel:e.target.value})}><option value="">Выберите Gemma 4</option>{options(settings.visionModel)}</select></label>
     <p className="settings-note">Модель изображений: {settings.imageModel}. Для её запуска нужен отдельный локальный сервер генерации.</p>
    <div className="settings-unchanged">Веб-поиск отключён. Скорость локальной генерации зависит от установленной модели и компьютера.</div>
    <button className="settings-save" type="submit">{pending?'Проверяем…':'Сохранить настройки'}</button>
   </fieldset>
  </form>}
  {message && <p role="status" className="settings-message">{message}</p>}
 </section>;
}
