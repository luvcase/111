"use strict";
const $ = id => document.getElementById(id);
const names = {concise:"速答",explain:"详细解析",code:"编程题",choice:"选项分析",visual:"图形推理",shenlun:"申论作答"};
const states = {queued:"等待处理",processing:"正在分析",done:"已完成",error:"处理失败"};
const builtinPrompts = [
  {name:"先答后析",text:"先给出直接答案，再逐条说明依据。"},
  {name:"排除法",text:"逐项检查选项，明确排除理由，不遗漏多选情况。"},
  {name:"编程复盘",text:"使用 Python 3，提供边界用例、时间和空间复杂度，解释核心实现。"},
  {name:"图形规律",text:"分别检查数量、位置、旋转、对称与叠加规律；说明哪些规律无法验证。"},
  {name:"申论 · 材料与格式",text:"严格依据给定材料和题目要求作答。概括题分类列要点，应用文按指定身份、对象与格式组织，文章写作紧扣中心论点。缺少材料时明确指出，不编造材料、政策、数据或引用。遵守字数限制，answer 只放作答正文。"}
];
let token = sessionStorage.getItem("screenbridge-token") || localStorage.getItem("screenbridge-account-token") || "";
let socket=null,reconnectTimer=null,generation=0,imageFile=null,imageJob=null,ignoredImage=null;
let models=[],modelPresets=[],prefs={model_ids:["default"],mode:"concise",prompt:"",question_type:"auto",collaborate:false,right_hold:false,current_job_id:null,custom_prompts:[]};
const effortNames={auto:"自动",low:"低",medium:"中",high:"高",xhigh:"更高"};
const records=new Map(), tests=new Map(), ocrConsumed=new Set(), detailStates=new Map();
let lastMobileView="";
let originalUrl=null;
const fragment=new URLSearchParams(location.hash.slice(1)).get("token");
if(fragment){token=fragment;history.replaceState(null,"",location.pathname);}
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function connection(text,online=false){$("connection").textContent=text;$("connection").classList.toggle("online",online);}
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{...(options.body instanceof FormData?{}:{"Content-Type":"application/json"}),Authorization:`Bearer ${token}`,...options.headers}});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"请求参数无效或服务暂不可用");
  return data;
}
function optionsFromUI(){return {model_ids:models.filter(m=>document.querySelector(`input[data-model="${m.id}"]`)?.checked).map(m=>m.id),mode:$("mode").value,prompt:$("prompt").value,question_type:$("question-type").value,collaborate:$("collaborate").checked};}
async function savePrefs(){
  try{prefs=await api("/api/workspace",{method:"PUT",body:JSON.stringify({...prefs,...optionsFromUI(),right_hold:$("right-hold").checked})});$("settings-status").textContent="设置已同步";return true;}
  catch(e){$("settings-status").textContent=e.message;return false;}
}
function applyPrefs(value){
  prefs=value;
  for(const [id,key] of [["mode","mode"],["prompt","prompt"],["question-type","question_type"]])if(document.activeElement!==$(id))$(id).value=prefs[key];
  $("collaborate").checked=prefs.collaborate;$("right-hold").checked=prefs.right_hold;
  renderModels();renderPrompts();render();
}
function renderPrompts(){
  const select=$("prompt-library"),old=select.value;select.replaceChildren(node("option","自定义 / 无附加提示词"));select.firstChild.value="";
  [...builtinPrompts,...prefs.custom_prompts].forEach((p,i)=>{const option=node("option",p.name);option.value=String(i);select.append(option);});select.value=old;
}
async function loadModels(){const data=await api("/api/models");models=data.models;modelPresets=data.presets||[];renderModels();}
function editModel(model){
  $("model-id").value=model?.id||"";$("model-name").value=model?.name||"";$("model-url").value=model?.base_url||"https://api.openai.com/v1";$("model-name-api").value=model?.model||"gpt-4.1-mini";$("model-key").value="";$("model-error").textContent="";
  $("reasoning-effort").value=model?.reasoning_effort||"auto";$("output-budget").value=model?.max_output_tokens||16384;$("use-saved-model").checked=true;
  $("compact-output").checked=!!model?.compact_output;$("qwen-thinking").value=model?.qwen_thinking||"auto";
  $("model-preset").replaceChildren(node("option","手动填写 / 其他模型"));$("model-preset").firstChild.value="";
  modelPresets.forEach(p=>{const option=node("option",`${p.label} · ${p.model}`);option.value=p.model;$("model-preset").append(option);});
  $("model-preset").value=modelPresets.some(p=>p.model===$("model-name-api").value)?$("model-name-api").value:"";
  resetCatalog();$("model-dialog").showModal();
}
function resetCatalog(){const option=node("option","先读取账号可用模型");option.value="";$("available-model").replaceChildren(option);$("available-model").disabled=true;$("catalog-status").textContent="";}
function renderModels(){
  $("active-model").textContent="当前使用："+models.filter(m=>prefs.model_ids.includes(m.id)).map(m=>`${m.model}（推理 ${effortNames[m.reasoning_effort||"auto"]}）`).join("、");
  $("model-list").replaceChildren();
  for(const model of models){
    const row=node("div",undefined,"model-row"),label=node("label",undefined,"model-choice"),input=node("input");input.type="checkbox";input.dataset.model=model.id;input.checked=prefs.model_ids.includes(model.id);
    input.addEventListener("change",()=>{const ids=optionsFromUI().model_ids;if(ids.length<1||ids.length>3){input.checked=!input.checked;$("settings-status").textContent="请选择 1–3 套模型配置";return;}savePrefs();});
    const title=node("span",model.name);title.append(node("span",`${model.model} · 推理 ${effortNames[model.reasoning_effort||"auto"]} · ${model.configured?"Key 已配置":"Key 未配置"}`,"model-meta"));label.append(input,title);row.append(label);
    const actions=node("div",undefined,"model-actions");
    const test=node("button","测试连接","text-button");test.addEventListener("click",async()=>{test.disabled=true;tests.set(model.id,"正在连接…");renderModels();try{const result=await api(`/api/models/${model.id}/test`,{method:"POST"});tests.set(model.id,result.message);}catch(e){tests.set(model.id,e.message);}renderModels();});
    const edit=node("button","编辑","text-button");edit.addEventListener("click",()=>editModel(model));actions.append(test,edit);
    if(model.id!=="default"){const del=node("button","删除","text-button");del.addEventListener("click",async()=>{if(!confirm(`删除配置“${model.name}”？`))return;try{await api(`/api/models/${model.id}`,{method:"DELETE"});await loadModels();}catch(e){$("settings-status").textContent=e.message;}});actions.append(del);}
    row.append(actions,node("div",tests.get(model.id)||"", "model-test-result"));$("model-list").append(row);
  }
}
function richText(text){const wrap=node("div",undefined,"answer");text.split(/```/).forEach((chunk,i)=>{if(i%2)wrap.append(node("pre",chunk.replace(/^\w*\n/,"")));else wrap.append(document.createTextNode(chunk));});return wrap;}
function folded(title,content,key){
  const detail=node("details",undefined,"result-details"),summary=node("summary",title);detail.append(summary,content);detail.open=detailStates.get(key)||false;detail.addEventListener("toggle",()=>detailStates.set(key,detail.open));return detail;
}
function renderResult(result,operation,key){
  const card=node("section",undefined,"model-result");card.append(node("h3",result.model_name||"综合建议"));
  if(result.status&&result.status!=="done"){card.append(node("p",result.error||states[result.status],result.status==="error"?"error":"pending"));return card;}
  if(operation!=="ocr")card.append(node("div","答案","result-label"),richText(result.answer||""));
  else if(result.question)card.append(node("div","OCR 转录","result-label"),node("div",result.question,"question-text"));
  if(result.cautions?.length)card.append(node("div","注意事项","result-label"),node("div",result.cautions.join("\n"),"cautions"));
  const copy=node("button",operation==="ocr"?"复制文字":"复制答案","copy-button");copy.addEventListener("click",async()=>{const text=operation==="ocr"?result.question:result.answer;try{await navigator.clipboard.writeText(text||"");copy.textContent="已复制";}catch{copy.textContent="请长按文本选择复制";}});card.append(copy);
  if(result.steps?.length){const list=node("ol",undefined,"steps");result.steps.forEach(s=>list.append(node("li",s)));card.append(folded("解析与复核",list,key+"-steps"));}
  if(operation!=="ocr"&&result.question)card.append(folded("查看题意（默认收起）",node("div",result.question,"question-text"),key+"-question"));
  const info=node("div");if(result.api_model||result.requested_model)info.append(node("p",`模型：${result.api_model||result.requested_model}${result.reasoning_effort?` · 推理设置 ${effortNames[result.reasoning_effort]||result.reasoning_effort}`:""}`,"hint"));
  if(result.api_model&&result.requested_model&&result.api_model!==result.requested_model)info.append(node("p",`请求模型 ID：${result.requested_model}`,"hint"));
  if(result.elapsed_ms!==undefined)info.append(node("p",`${(result.elapsed_ms/1000).toFixed(1)} 秒`,"hint"));
  const d=result.diagnostics;
  if(d){info.append(node("p",`接口耗时 ${(d.api_ms/1000).toFixed(1)} 秒 · 排队 ${((result.queue_wait_ms||0)/1000).toFixed(1)} 秒 · 重试等待 ${(d.retry_wait_ms/1000).toFixed(1)} 秒 · 请求 ${d.attempts} 次`,"hint"));
    info.append(node("p",`实际发送预算 ${d.output_budget} · ${d.compact_output?"简短输出":"完整输出"} · 千问思考 ${d.qwen_thinking}`,"hint"));
    const counts=[["输入",d.input_tokens],["输出",d.output_tokens],["推理",d.reasoning_tokens]].filter(([,v])=>v!==undefined).map(([name,v])=>`${name} ${v}`).join(" · ");if(counts)info.append(node("p",counts+" token","hint"));
    info.append(node("p",d.model_confirmed?"模型 ID 来自接口响应":"接口未返回模型 ID，上方展示的是请求配置","hint"));
    if(d.request_id)info.append(node("p","请求 ID："+d.request_id,"hint"));if(d.endpoint_host)info.append(node("p","接口域名："+d.endpoint_host,"hint"));
  }
  if(info.childNodes.length)card.append(folded("模型与耗时",info,key+"-info"));return card;
}
function preview(src){$("image-preview").src=src||"";$("image-preview").hidden=!src;$("preview-empty").hidden=!!src;$("crop-image").disabled=!src;$("view-original").disabled=!src;$("clear-image").disabled=!src;}
function render(){
  const list=[...records.values()].sort((a,b)=>b.created_at-a.created_at).slice(0,30);$("job-count").textContent=`${list.length} 条`;$("history-list").replaceChildren();
  const active=records.get(prefs.current_job_id)||list[0];
  for(const job of list){const button=node("button",undefined,`history-button ${job===active?"selected":""}`);button.append(node("span",`${job.operation==="ocr"?"OCR":names[job.mode]} · ${states[job.status]}`),node("span",new Date(job.created_at*1000).toLocaleTimeString(),"history-time"));button.addEventListener("click",async()=>{prefs.current_job_id=job.id;imageFile=null;ignoredImage=null;await savePrefs();render();});$("history-list").append(button);}
  $("jobs").replaceChildren();$("empty").hidden=!!active;if(!active)return;
  if(!imageFile&&ignoredImage!==active.id){imageJob=active.thumbnail?active.id:null;preview(active.thumbnail||"");}
  const card=node("article",undefined,"card job"),head=node("div",undefined,"job-header"),title=node("div",active.operation==="ocr"?"图片转文字":`${names[active.mode]} · ${active.results.length} 模型`,"job-title");title.append(node("span",new Date(active.created_at*1000).toLocaleString(),"job-time"));head.append(title,node("span",active.partial?"部分完成":states[active.status],`badge ${active.status==="error"?"error":""}`));card.append(head);
  const grid=node("div",undefined,"comparison");active.results.forEach((r,i)=>grid.append(renderResult(r,active.operation,active.id+"-"+i)));card.append(grid);
  if(active.synthesis){const section=node("div",undefined,"synthesis");section.append(node("h3","协同综合建议"),renderResult(active.synthesis,"analyze",active.id+"-synthesis"));card.append(section);}
  if(active.synthesis_error)card.append(node("p",active.synthesis_error,"error"));
  if(active.image_info){const im=active.image_info;card.append(folded("提交图片规格",node("p",`${im.width} × ${im.height} · ${im.mime} · ${(im.bytes/1024).toFixed(1)} KB · 缩略图不用于模型分析`,"hint"),active.id+"-image"));}
  if(active.input_text)card.append(folded("输入题目与材料",node("div",active.input_text,"question-text"),active.id+"-input"));
  if(active.operation==="ocr"&&active.status==="done"&&!ocrConsumed.has(active.id)){ocrConsumed.add(active.id);if(!$("question").value.trim())$("question").value=active.answer;}
  if(active.status==="done"||active.status==="error"){const del=node("button","删除此记录与题图","text-button");del.addEventListener("click",async()=>{if(!confirm("删除这条复盘记录和题图？"))return;try{await api(`/api/jobs/${active.id}`,{method:"DELETE"});}catch(e){$("upload-status").textContent=e.message;}});card.append(del);}
  $("jobs").append(card);
  const mobileView=active.id+":"+active.status+":"+active.results.map(r=>r.status).join(",");
  if(matchMedia("(max-width: 700px)").matches&&mobileView!==lastMobileView){lastMobileView=mobileView;requestAnimationFrame(()=>{if(!document.querySelector("dialog[open]"))$("jobs").scrollIntoView({block:"start",behavior:"auto"});});}
}
function connectSocket(){
  clearTimeout(reconnectTimer);if(!token)return;const current=generation;
  const ws=new WebSocket(`${location.protocol==="https:"?"wss:":"ws:"}//${location.host}/ws`);socket=ws;
  ws.onopen=()=>ws.send(JSON.stringify({token}));
  ws.onmessage=event=>{if(current!==generation)return;let data;try{data=JSON.parse(event.data);}catch{return;}connection("● 已连接",true);
    if(data.type==="snapshot"){records.clear();data.jobs.forEach(j=>records.set(j.id,j));applyPrefs(data.workspace);}
    if(data.type==="job"){records.set(data.job.id,data.job);if(data.job.status==="queued"){prefs.current_job_id=data.job.id;imageFile=null;ignoredImage=null;}render();}
    if(data.type==="workspace")applyPrefs(data.workspace);
    if(data.type==="models")loadModels().catch(e=>$("settings-status").textContent=e.message);
    if(data.type==="deleted"){records.delete(data.id);if(imageJob===data.id){imageJob=null;preview("");}render();}
  };
  ws.onclose=event=>{if(current!==generation)return;if(event.code===4401){disconnect();$("pair-error").textContent="登录或配对已失效，请重新连接。";return;}connection("正在重连…");reconnectTimer=setTimeout(connectSocket,2000);};ws.onerror=()=>ws.close();
}
async function pair(value){
  try{const response=await fetch("/api/status",{headers:{Authorization:`Bearer ${value}`}});if(!response.ok)throw new Error("登录或配对令牌无效，请重新连接。");const status=await response.json();generation++;if(socket)socket.close();token=value;sessionStorage.setItem("screenbridge-token",token);if(status.account)localStorage.setItem("screenbridge-account-token",token);
    $("pairing").hidden=true;$("workspace").hidden=false;$("demo-banner").hidden=!status.demo;$("account-button").textContent=status.username;$("provider-status").textContent=`${status.username} · ${status.demo?"演示传输模式":"视觉分析工作台"}`;$("pair-error").textContent="";imageFile=null;imageJob=null;ignoredImage=null;records.clear();tests.clear();ocrConsumed.clear();await loadModels();applyPrefs(await api("/api/workspace"));connectSocket();
  }catch(e){$("pair-error").textContent=e.message;connection("未连接");}
}
function disconnect(){generation++;token="";lastMobileView="";detailStates.clear();sessionStorage.removeItem("screenbridge-token");localStorage.removeItem("screenbridge-account-token");clearTimeout(reconnectTimer);if(socket)socket.close();records.clear();imageFile=null;imageJob=null;preview("");render();$("workspace").hidden=true;$("pairing").hidden=false;$("token").value="";$("account-button").textContent="账号登录";connection("未连接");}
async function imageBlob(){
  if(imageFile)return imageFile;
  if(!imageJob)return null;
  const response=await fetch(`/api/jobs/${imageJob}/image`,{headers:{Authorization:`Bearer ${token}`}});if(!response.ok)throw new Error("原图已不存在，请重新导入");return response.blob();
}
async function submit(operation){
  $("analyze").disabled=true;$("ocr").disabled=true;$("upload-status").textContent="正在提交…";
  try{if(!await savePrefs())throw new Error($("settings-status").textContent);const opts=optionsFromUI(),text=$("question").value.trim(),image=await imageBlob();let job;
    if(image){const form=new FormData();form.append("image",image,"question.jpg");form.append("model_ids",JSON.stringify(opts.model_ids));for(const key of ["mode","prompt","question_type","collaborate"])form.append(key,String(opts[key]));form.append("text",text);form.append("operation",operation);job=await api("/api/screenshots",{method:"POST",body:form});}
    else{if(operation==="ocr")throw new Error("请先截图或导入图片");if(!text)throw new Error("请导入题图或输入题目");job=await api("/api/analyze",{method:"POST",body:JSON.stringify({...opts,text})});}
    prefs.current_job_id=job.id;if(!records.has(job.id))records.set(job.id,job);imageFile=null;ignoredImage=null;render();$("upload-status").textContent="已提交，分析结果会自动同步。";
  }catch(e){$("upload-status").textContent=e.message;}finally{$("analyze").disabled=false;$("ocr").disabled=false;}
}
$("pair-form").addEventListener("submit",e=>{e.preventDefault();pair($("token").value.trim());});
$("disconnect").addEventListener("click",async()=>{try{await api("/api/auth/logout",{method:"POST"});}catch{}disconnect();});
$("image-input").addEventListener("change",e=>{const file=e.target.files[0];if(!file)return;if(file.size>8*1024*1024){$("upload-status").textContent="图片不能超过 8 MB";return;}const reader=new FileReader();reader.onload=()=>{imageFile=file;imageJob=null;preview(reader.result);$("upload-status").textContent="题图已导入，可直接分析或 OCR。";};reader.readAsDataURL(file);e.target.value="";});
$("clear-image").addEventListener("click",()=>{imageFile=null;imageJob=null;ignoredImage=prefs.current_job_id;preview("");});
$("analyze").addEventListener("click",()=>submit("analyze"));$("ocr").addEventListener("click",()=>submit("ocr"));
for(const id of ["mode","right-hold","collaborate","prompt"])$(id).addEventListener("change",savePrefs);
$("question-type").addEventListener("change",()=>{if($("question-type").value==="shenlun")$("mode").value="shenlun";savePrefs();});
$("prompt-library").addEventListener("change",()=>{const p=[...builtinPrompts,...prefs.custom_prompts][Number($("prompt-library").value)];$("prompt").value=$("prompt-library").value===""?"":p.text;savePrefs();});
$("save-prompt").addEventListener("click",()=>{if(!$("prompt").value.trim()){ $("settings-status").textContent="请先填写提示词";return;}const name=window.prompt("为提示词取一个名字");if(!name)return;prefs.custom_prompts.push({name,text:$("prompt").value});savePrefs().then(()=>renderPrompts());});
$("new-model").addEventListener("click",()=>editModel(null));
$("change-model").addEventListener("click",()=>editModel(models.find(m=>prefs.model_ids.includes(m.id))||models[0]));
$("model-preset").addEventListener("change",()=>{const preset=modelPresets.find(p=>p.model===$("model-preset").value);if(!preset)return;$("model-name-api").value=preset.model;$("model-name").value=preset.label;$("reasoning-effort").value=preset.model.startsWith("gpt-6")?"high":"auto";$("output-budget").value=16384;});
$("model-name-api").addEventListener("input",()=>{$("model-preset").value=modelPresets.some(p=>p.model===$("model-name-api").value)?$("model-name-api").value:"";});
for(const id of ["model-url","model-key"])$(id).addEventListener("input",resetCatalog);
$("load-catalog").addEventListener("click",async()=>{const button=$("load-catalog");button.disabled=true;$("catalog-status").textContent="正在读取…";try{const data=await api("/api/model-catalog",{method:"POST",body:JSON.stringify({id:$("model-id").value||null,base_url:$("model-url").value,api_key:$("model-key").value})});const select=$("available-model"),first=node("option","选择账号返回的模型 ID");first.value="";select.replaceChildren(first);data.models.forEach(id=>{const option=node("option",id);option.value=id;select.append(option);});select.disabled=!data.models.length;$("catalog-status").textContent=`已读取 ${data.models.length} 个模型。${data.message} ${data.models.includes($("model-name-api").value)?"当前模型已列出。":"当前填写的模型未列出，请核对账号权限。"}`;}catch(e){resetCatalog();$("catalog-status").textContent=e.message;}finally{button.disabled=false;}});
$("available-model").addEventListener("change",()=>{const id=$("available-model").value;if(!id)return;$("model-name-api").value=id;$("model-preset").value=modelPresets.some(p=>p.model===id)?id:"";if($("model-preset").value)$("model-preset").dispatchEvent(new Event("change"));else if(!$("model-name").value)$("model-name").value=id;});
$("speed-preset").addEventListener("click",()=>{$("output-budget").value=2048;$("compact-output").checked=true;});
$("model-form").addEventListener("submit",async e=>{e.preventDefault();try{const saved=await api("/api/models",{method:"POST",body:JSON.stringify({id:$("model-id").value||null,name:$("model-name").value,base_url:$("model-url").value,model:$("model-name-api").value,api_key:$("model-key").value,reasoning_effort:$("reasoning-effort").value,max_output_tokens:Number($("output-budget").value),compact_output:$("compact-output").checked,qwen_thinking:$("qwen-thinking").value})});const apply=$("use-saved-model").checked;$("model-key").value="";$("model-dialog").close();if(apply)prefs.model_ids=[saved.id];await loadModels();if(apply)await savePrefs();}catch(err){$("model-error").textContent=err.message;}});
$("view-original").addEventListener("click",async()=>{try{const blob=await imageBlob();if(!blob)return;if(originalUrl)URL.revokeObjectURL(originalUrl);originalUrl=URL.createObjectURL(blob);const image=$("original-image");image.src=originalUrl;await image.decode();$("original-info").textContent=`${image.naturalWidth} × ${image.naturalHeight} · ${(blob.size/1024).toFixed(1)} KB。按原始像素显示，可在框内滚动；放大不能恢复原图已缺失的细节。`;$("original-dialog").showModal();}catch(e){$("upload-status").textContent=e.message;}});
$("original-dialog").addEventListener("close",()=>{if(originalUrl)URL.revokeObjectURL(originalUrl);originalUrl=null;$("original-image").removeAttribute("src");});
for(const id of ["account-button","login-from-pair"])$(id).addEventListener("click",()=>{$("account-error").textContent="";$("account-dialog").showModal();});
$("account-form").addEventListener("submit",async e=>{e.preventDefault();const action=e.submitter?.value||"login";try{const data=await api(`/api/auth/${action}`,{method:"POST",body:JSON.stringify({username:$("username").value,password:$("password").value})});$("password").value="";$("account-dialog").close();await pair(data.token);}catch(err){$("account-error").textContent=err.message;}});
$("link-desktop").addEventListener("click",async()=>{try{await api("/api/desktop/link",{method:"POST"});$("upload-status").textContent="正在绑定本机截图客户端，稍后可使用快捷键。";}catch(e){$("upload-status").textContent=e.message;}});
for(const kind of ["region","full"])$("capture-"+kind).addEventListener("click",async()=>{try{await api("/api/desktop/capture",{method:"POST",body:JSON.stringify({kind})});$("upload-status").textContent="2 秒后截图，请切换到题目窗口。";}catch(e){$("upload-status").textContent=e.message;}});
document.querySelectorAll(".close-dialog").forEach(button=>button.addEventListener("click",()=>button.closest("dialog").close()));
$("crop-image").addEventListener("click",async()=>{
  try{const blob=await imageBlob();if(!blob)return;const bitmap=await createImageBitmap(blob),canvas=$("crop-canvas"),ctx=canvas.getContext("2d");canvas.width=bitmap.width;canvas.height=bitmap.height;ctx.drawImage(bitmap,0,0);$("crop-dialog").showModal();let start=null;
    const point=e=>{const r=canvas.getBoundingClientRect();return {x:Math.max(0,Math.min(canvas.width,(e.clientX-r.left)*canvas.width/r.width)),y:Math.max(0,Math.min(canvas.height,(e.clientY-r.top)*canvas.height/r.height))};};
    canvas.onpointerdown=e=>{start=point(e);canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(!start)return;const end=point(e);ctx.drawImage(bitmap,0,0);ctx.strokeStyle="#21a878";ctx.lineWidth=Math.max(2,canvas.width/300);ctx.strokeRect(start.x,start.y,end.x-start.x,end.y-start.y);};
    canvas.onpointerup=e=>{if(!start)return;const end=point(e),x=Math.min(start.x,end.x),y=Math.min(start.y,end.y),w=Math.abs(end.x-start.x),h=Math.abs(end.y-start.y);start=null;if(w<5||h<5)return;const cropped=document.createElement("canvas");cropped.width=Math.round(w);cropped.height=Math.round(h);cropped.getContext("2d").drawImage(bitmap,x,y,w,h,0,0,w,h);cropped.toBlob(blob=>{imageFile=blob;imageJob=null;preview(cropped.toDataURL("image/png"));$("crop-dialog").close();},"image/png");};canvas.onpointercancel=()=>{start=null;};
  }catch(e){$("upload-status").textContent=e.message;}
});
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&token&&(!socket||socket.readyState>1))connectSocket();});
if(token)pair(token);
