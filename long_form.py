"""Separate private-only 4–6 minute Digital Orbit video pipeline."""
import asyncio, html, json, os, re, shutil, subprocess
from pathlib import Path
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from main import fetch_multi_source_news, fetch_source_article_text, get_api_keys, generate_script
from news_quality_gate import is_near_duplicate
from long_video_upload import upload_long_video_private

OUT=Path("long_form_output"); OUT.mkdir(exist_ok=True)
W,H=1920,1080
MIN_WORDS,MAX_WORDS=520,850
VOICE=os.getenv("LONG_VIDEO_VOICE","en-US-AndrewNeural")

def clean(s):
    return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s or ""))).strip()

def choose_story(entries):
    chosen=[]
    for item in entries:
        title=clean(item.get("title","")); summary=clean(item.get("summary","") or item.get("description",""))
        if len(title.split())<4 or len(summary.split())<18 or not item.get("link"): continue
        if any(is_near_duplicate(title,x[0]) for x in chosen): continue
        chosen.append((title,item,summary))
    return chosen[0] if chosen else (None,None,None)

def narration(title,summary,article):
    prompt=f"""Write a neutral, natural American-English Digital Orbit voiceover for a 4–6 minute YouTube news explainer, 600–750 words (absolute 520–850). Use ONLY facts in the supplied publisher material. Never invent facts, quotes, numbers, dates, causes or outcomes. Attribute disputed/developing claims. If fewer than 520 factual words are supportable, return exactly INSUFFICIENT_SOURCE. Structure: clear hook, what happened, verified details/timeline, who is affected, supported context, what remains unknown/next, concise close. No clickbait, predictions or headings. Narration only.
HEADLINE: {title}
RSS SUMMARY: {summary[:3500]}
PUBLISHER ARTICLE EXTRACT: {article[:9000]}"""
    script=clean(generate_script(get_api_keys(),prompt))
    count=len(script.split())
    if script=="INSUFFICIENT_SOURCE" or not MIN_WORDS<=count<=MAX_WORDS:
        raise RuntimeError(f"Script rejected: {count} words; require {MIN_WORDS}-{MAX_WORDS} sourced words.")
    return script

async def voice(script,path):
    await asyncio.wait_for(edge_tts.Communicate(script,VOICE,rate="-5%").save(str(path)),timeout=240)
    if not path.exists() or path.stat().st_size<10000: raise RuntimeError("TTS audio missing/undersized.")

def font(size,bold=False):
    p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p,size) if Path(p).exists() else ImageFont.load_default()

def wrap(draw,text,fnt,width):
    out=[]; line=""
    for word in text.split():
        test=(line+" "+word).strip()
        if draw.textbbox((0,0),test,font=fnt)[2]<=width: line=test
        else:
            if line: out.append(line)
            line=word
    if line: out.append(line)
    return out

def scenes(title,script):
    sentences=re.split(r"(?<=[.!?])\s+",script)
    chunks=[]; current=[]
    for s in sentences:
        if len(" ".join(current+[s]).split())>70 and current:
            chunks.append(" ".join(current)); current=[]
        current.append(s)
    if current: chunks.append(" ".join(current))
    n=min(8,max(5,len(chunks)))
    groups=[" ".join(chunks[i::n]) for i in range(n)]
    labels=["THE DEVELOPMENT","KEY DETAILS","THE TIMELINE","WHO IS AFFECTED","THE BIGGER PICTURE","WHAT WE KNOW","WHAT REMAINS UNCLEAR","WHAT COMES NEXT"]
    files=[]
    for i,body in enumerate(groups):
        im=Image.new("RGB",(W,H),(12+i*2,24+i*2,42+i*3)); d=ImageDraw.Draw(im)
        d.rectangle((0,0,W,18),fill=(48,125,190))
        d.text((110,100),"DIGITAL ORBIT  |  USA NEWS EXPLAINER",font=font(30,True),fill=(150,190,220))
        d.text((110,235),labels[i],font=font(38,True),fill=(105,190,255))
        for j,line in enumerate(wrap(d,title,font(62,True),1660)[:3]):
            d.text((110,320+j*78),line,font=font(62,True),fill="white")
        for j,line in enumerate(wrap(d,body,font(37),1640)[:7]):
            d.text((115,650+j*55),line,font=font(37),fill=(220,230,240))
        d.text((110,1020),"CLEAR FACTS. IMPORTANT STORIES.",font=font(24,True),fill=(150,175,200))
        p=OUT/f"scene_{i+1:02}.jpg"; im.save(p,quality=92); files.append(p)
    return files

def stamp(t):
    ms=int(t*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def subtitles(script,duration,path):
    parts=re.split(r"(?<=[.!?])\s+",script); groups=[]; cur=""
    for p in parts:
        if len((cur+" "+p).split())>16 and cur: groups.append(cur); cur=""
        cur=(cur+" "+p).strip()
    if cur: groups.append(cur)
    lines=[]
    for i,p in enumerate(groups):
        lines += [str(i+1),f"{stamp(duration*i/len(groups))} --> {stamp(duration*(i+1)/len(groups))}",p,""]
    path.write_text("\n".join(lines),encoding="utf-8")

def render(cards,audio,script,out):
    probe=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)],capture_output=True,text=True,check=True,timeout=30)
    duration=float(probe.stdout.strip())
    if not 240<=duration<=420: raise RuntimeError(f"Audio duration {duration:.1f}s outside 4–7 minute safety range.")
    srt=OUT/"captions.srt"; subtitles(script,duration,srt)
    concat=OUT/"scenes.txt"; per=duration/len(cards)
    concat.write_text("".join(f"file '{p.resolve()}'\nduration {per:.4f}\n" for p in cards)+f"file '{cards[-1].resolve()}'\n",encoding="utf-8")
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-i",str(audio),"-vf",
        "subtitles=captions.srt:force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=55'",
        "-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p","-r","25","-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(out)],
        check=True,timeout=900,capture_output=True,text=True)
    return duration

def validate(path):
    r=subprocess.run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True,check=True,timeout=30)
    data=json.loads(r.stdout); streams=data.get("streams",[])
    v=next((x for x in streams if x.get("codec_type")=="video"),None); a=next((x for x in streams if x.get("codec_type")=="audio"),None)
    duration=float(data.get("format",{}).get("duration",0))
    if not v or not a or int(v["width"])<=int(v["height"]): raise RuntimeError("Need landscape video and audio streams.")
    if abs(int(v["width"])/int(v["height"])-16/9)>0.02: raise RuntimeError("Video is not 16:9.")
    if not 240<=duration<=420 or path.stat().st_size<500000: raise RuntimeError("Rendered video failed duration/size gate.")
    return duration

def main():
    for tool in ("ffmpeg","ffprobe"):
        if not shutil.which(tool): raise RuntimeError(f"Missing {tool}")
    title,item,summary=choose_story(fetch_multi_source_news())
    if not item: raise RuntimeError("No sufficiently detailed RSS story.")
    source=item.get("link",""); article=fetch_source_article_text(source)
    if len((summary+" "+article).split())<180: raise RuntimeError("Insufficient publisher source material; skipping, no padding.")
    script=narration(title,summary,article); (OUT/"script.txt").write_text(script,encoding="utf-8")
    audio=OUT/"voice.mp3"; asyncio.run(voice(script,audio))
    cards=scenes(title,script); video=OUT/"digital_orbit_long_private.mp4"
    render(cards,audio,script,video); duration=validate(video)
    report={"headline":title,"source":source,"word_count":len(script.split()),"duration_seconds":round(duration,1),"visual_scenes":len(cards),"privacy":"private"}
    (OUT/"quality_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print("LONG VIDEO QUALITY GATE PASSED:",json.dumps(report))
    upload_long_video_private(video,title,source)

if __name__=="__main__": main()
