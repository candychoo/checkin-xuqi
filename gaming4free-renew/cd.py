import re
from util import log
def chk_cd(dr):
    try:
        pt=dr.execute_script("return document.body?document.body.innerText.substring(0,2000):'';")
        if not pt: return None
        em=re.search(r'expires\s+(\d{1,2}:\d{2})(?!\s*[APap][Mm])',pt,re.I)
        if em:
            h,m=em.group(1).split(':')
            r=int(h)*3600+int(m)*60
            log(f"⏳ 冷却(expires): {em.group(1)} ({r}s)")
            return{'cooldown':True,'remaining':r,'text':em.group(1)}
        cm=re.search(r'(\d+):(\d+)\s+cd',pt,re.I)
        if cm:
            r=int(cm.group(1))*60+int(cm.group(2))
            log(f"⏳ 冷却(cd): {cm.group(0).strip()} ({r}s)")
            return{'cooldown':True,'remaining':r,'text':cm.group(0).strip()}
        try:
            dis=bool(dr.execute_script("""
                var b=document.querySelectorAll('button,[role=button]');
                for(var i=0;i<b.length;i++){
                    var t=(b[i].innerText||b[i].textContent||'').trim();
                    if(t.indexOf('90')!==-1)return b[i].disabled;
                }return false;"""))
            if dis:
                log("⏳ 按钮 disabled")
                return{'cooldown':True,'remaining':0,'text':'disabled'}
        except: pass
        return None
    except Exception as e:
        log(f"⚠️ 冷却检查失败: {e}")
        return None
