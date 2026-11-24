'use strict';
const token = document.querySelector('meta[name=docsync-token]').content;
const board = document.querySelector('#board');
const notice = document.querySelector('#notice');
const labels = {pending:'Chờ duyệt',approved:'Đã duyệt · chờ tải',downloading:'Đang tải',saved:'Đã lưu',retry:'Lỗi · sẽ thử lại',held:'Đã bỏ qua'};
const groups = [['Chờ duyệt',['pending']],['Đang xử lý',['approved','downloading','retry']],['Đã lưu',['saved']],['Bỏ qua',['held']]];
let loading = false;
let deciding = false;
let fingerprint = '';
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node; }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'X-CSRF-Token':token,...options.headers}});
  const data=await response.json();
  if(!response.ok)throw new Error(data.message||'Không cập nhật được. Bấm Cập nhật để thử lại.');
  return data;
}
async function decision(item, action, button) {
  if(deciding)return;
  deciding=true; board.querySelectorAll('button').forEach(node=>node.disabled=true);
  let failure='';
  try {await api('/api/telegram/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat:item.chat,message:item.message,action})});fingerprint='';}
  catch(error){failure=error.message;}
  finally {deciding=false;board.querySelectorAll('button').forEach(node=>node.disabled=false);await refresh();if(failure)notice.textContent=failure;}
}
function render(data) {
  const next=JSON.stringify(data);
  if(next===fingerprint)return;
  fingerprint=next;
  board.replaceChildren();
  for(const [title,statuses] of groups){
    const column=el('section',undefined,'column');
    const count=statuses.reduce((n,s)=>n+(data.counts[s]||0),0);
    const heading=el('h2',title);heading.append(el('span',String(count),'count'));column.append(heading);
    const items=data.items.filter(item=>statuses.includes(item.status));
    if(!items.length)column.append(el('p','Chưa có file.','empty'));
    for(const item of items){
      const card=el('article',undefined,'card');
      card.append(el('div',item.name,'filename'));
      card.append(el('div',(item.bytes==null?'Chưa rõ dung lượng':(item.bytes/1024/1024).toLocaleString('vi-VN',{maximumFractionDigits:2})+' MB')+' · Tin #'+item.message,'meta'));
      card.append(el('div',labels[item.status],'status'));
      if(item.error)card.append(el('p',item.error==='SourceChanged'?'File trên Telegram đã thay đổi. Cần duyệt lại.':item.error==='ValueError'?'File gốc không còn truy cập được. Có thể chọn Bỏ qua.':'Lượt trước gặp lỗi; máy sẽ thử lại sau.','error'));
      const actions=el('div',undefined,'actions');
      if(['pending','held'].includes(item.status)){const button=el('button','Duyệt để tải');button.onclick=()=>decision(item,'approve',button);actions.append(button);}
      if(['pending','approved','retry'].includes(item.status)){const button=el('button',item.status==='approved'?'Hủy duyệt':'Bỏ qua','secondary');button.onclick=()=>decision(item,'hold',button);actions.append(button);}
      card.append(actions);column.append(card);
    }
    board.append(column);
  }
}
async function refresh(){
  if(loading||deciding)return;
  loading=true;
  try{const data=await api('/api/telegram/items');notice.textContent=data.ready?'Chọn từng file để duyệt. Chưa duyệt thì chưa tải.':'Chưa đăng nhập Telegram. Hoàn tất cửa sổ thiết lập để nhận danh sách file chờ duyệt.';render(data);}
  catch(error){notice.textContent=error.message;}
  finally{loading=false;}
}
document.querySelector('#refresh').onclick=refresh;
refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
