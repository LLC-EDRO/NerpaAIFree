import {useEffect,useState} from 'react';
import './ModelSettings.css';
type Settings={provider:'default'|'openwebui';baseUrl:string;textModel:string;visionModel:string;keyConfigured:boolean};
type Model={id:string;name:string};
async function request<T>(path:string,body?:unknown,method='POST'):Promise<T>{
 const response=await fetch('/api/settings/models'+path,body===undefined?undefined:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw Error(data.error?.message || 'Не удалось выполнить запрос');return data;
}
export function ModelSettings({onBack,onSaved}:{onBack:()=>void;onSaved:()=>void}){
 const [settings,setSettings]=useState<Settings>();
 const [apiKey,setApiKey]=useState(''),[clearApiKey,setClearApiKey]=useState(false);
 const [models,setModels]=useState<Model[]>([]),[pending,setPending]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let live=true;request<Settings>('').then(s=>{if(live)setSettings(s);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[]);
 const change=(patch:Partial<Settings>)=>{setSettings(s=>s?{...s,...patch}:s);setMessage('');};
 async function run(action:'discover'|'save'){
  if(!settings)return;setPending(true);setMessage('');setError('');
  try{
   const body={...settings,apiKey:apiKey || undefined,clearApiKey};
   if(action==='discover'){
    const result=await request<{models:Model[]}>('/discover',body);setModels(result.models);setMessage(`Доступно моделей: ${result.models.length}. Выберите модель и сохраните настройки.`);
   }else{
    const saved=await request<Settings>('',body,'PUT');setSettings(saved);setApiKey('');setClearApiKey(false);setMessage('Сохранено. Выбор применяется к новым экспериментам.');onSaved();
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
    <label>Провайдер генерации<select value={settings.provider} onChange={e=>change({provider:e.target.value as Settings['provider']})}><option value="default">Текущая конфигурация сервера</option><option value="openwebui">Open WebUI / Ollama</option></select></label>
    <p className="settings-note">Текстовая модель составляет план, заполняет поля и проверяет содержание. Визуальная модель анализирует слайды и помогает исправлять расположение объектов.</p>
    {settings.provider==='openwebui' && <>
     <label>Адрес Open WebUI<input type="url" required value={settings.baseUrl} onChange={e=>{change({baseUrl:e.target.value,textModel:'',visionModel:''});setModels([]);}} placeholder="https://chatai.edro.su" /></label>
     <label>API-ключ Open WebUI<input type="password" autoComplete="off" value={apiKey} onChange={e=>{setApiKey(e.target.value);setClearApiKey(false);}} placeholder={settings.keyConfigured?'Ключ сохранён. Оставьте пустым, чтобы сохранить его':'Введите API-ключ'} /></label>
     <p className="settings-note">Ключ можно создать в Open WebUI: Settings → Account → API Keys. Он хранится только в backend на этом компьютере и не возвращается в браузер. Для доступа без авторизации поле можно оставить пустым.</p>
     {settings.keyConfigured && <label className="settings-check"><input type="checkbox" checked={clearApiKey} onChange={e=>setClearApiKey(e.target.checked)} />Удалить сохранённый ключ</label>}
     <button type="button" onClick={()=>void run('discover')}>Загрузить доступные модели</button>
     <label>Модель для текста<select required value={settings.textModel} onChange={e=>change({textModel:e.target.value})}><option value="">Выберите модель из списка</option>{options(settings.textModel)}</select></label>
     <label>Модель для анализа изображений слайдов<select value={settings.visionModel} onChange={e=>change({visionModel:e.target.value})}><option value="">Оставить текущую визуальную модель сервера</option>{options(settings.visionModel)}</select></label>
     <p className="settings-note">Для текста нужна чат-модель: embedding и reranker для генерации не подходят. Для визуального анализа выбирайте модель с поддержкой изображений. Список Open WebUI не подтверждает эту возможность. Чтобы весь анализ шёл локально, выберите здесь локальную vision-модель.</p>
    </>}
    <div className="settings-unchanged">Веб-поиск и генерация новых картинок используют прежние модели. Скорость локальной генерации зависит от модели и вашего сервера.</div>
    <button className="settings-save" type="submit">{pending?'Проверяем…':'Сохранить настройки'}</button>
   </fieldset>
  </form>}
  {message && <p role="status" className="settings-message">{message}</p>}
 </section>;
}
