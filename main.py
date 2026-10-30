import os, re, html, asyncio, subprocess, base64, time
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse
import feedparser, requests, edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageOps
from google import genai

RSS_URL="https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"
TEXT_MODEL="gemini-3.8-flash"
IMAGE_MODEL="gemini-nano-banana-2.1"
VOICES=["en-US-AndrewNeural","en-US-JennyNeural"]
MAX_STORIES=3
W,H=1080,1920
OUT=Path("output_videos"); OUT.mkdir(exist_ok=True)
S=requests.Session(); S.headers["User-Agent"]="Mozilla/5.0 DigitalOrbitNews/1.0"

def keys():
    out=[]
    for n in ("GOOGLE_API_KEY_1","GOOGLE_API_KEY_2","GOOGLE_API_KEY_3","GOOGLE_API_KEY"):
        v=os.getenv(n,"").strip()
        if v and v not in out: out.append(v)
    if not out: raise RuntimeError("No Google API key in GitHub Secrets.")
    return out

def script_for(ks,prompt):
    for attempt in range(3):
        for k in ks:
            try:
                t=genai.Client(api_key=k).models.generate_content(model=TEXT_MODEL,contents=prompt).text
                if t and t.strip(): return t.strip()
            except Exception as e: print("Text API error:",str(e)[:200])
        if attempt<2: time.sleep(5*(attempt+1))
    return ""

def font(n,b=False):
    p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if b else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    return ImageFont.truetype(p,n) if Path(p).exists() else ImageFont.load_default()

def image_from_rss(entry):
    urls=[]
    for group in ("media_content","media_thumbnail","enclosures"):
        for x in entry.get(group,[]) or []:
            u=x.get("url") or x.get("href")
            if u: urls.append(u)
    raw=entry.get("summary") or entry.get("description") or ""
    urls += [html.unescape(x) for x in re.findall(r'<img[^>]+src=["\\']([^"\\']+)',raw,re.I)]
    for u in dict.fromkeys(urls):
        try:
            if urlparse(u).scheme not in ("http","https"): continue
            r=S.get(u,timeout=12); r.raise_for_status()
            if not r.headers.get("Content-Type","").startswith("image/") or len(r.content)>12_000_000: continue
            im=ImageOps.exif_transpose(Image.open(BytesIO(r.content))).convert("RGB")
            if im.width>=250 and im.height>=150:
                print("Using original RSS image."); return im
        except Exception: pass
    print("No original RSS image found."); return None

def ai_image(ks,title,summary,n):
    prompt=f"""Create a photorealistic editorial news illustration for a US news short.
Headline: {title}
Context: {summary[:1200]}
Show a relevant, realistic documentary-news scene. Vertical 9:16 composition, professional photojournalism, natural lighting. No text, logos, watermarks, fake charts, invented signs, or fabricated claims. Treat it as an illustrative image, not documentary proof."""
    for i,k in enumerate(ks,1):
        try:
            client=genai.Client(api_key=k)
            result=client.interactions.create(model=IMAGE_MODEL,input=prompt,response_format={"type":"image","mime_type":"image/jpeg","aspect_ratio":"9:16","image_size":"1K"})
            data=result.output_image.data if result.output_image else None
            if not data: raise RuntimeError("No image in API response")
            im=Image.open(BytesIO(base64.b64decode(data))).convert("RGB")
            im.save(OUT/f"ai_news_image_{n:02}.jpg",quality=92)
            print("AI story image generated."); return im
        except Exception as e: print(f"Image generation attempt {i} failed:",str(e)[:220])
    return None

def lines(draw,text,f,maxw):
    result=[]; cur=""
    for word in text.split():
        test=(cur+" "+word).strip()
        if draw.textbbox((0,0),test,font=f)[2]<=maxw: cur=test
        else:
            if cur: result.append(cur)
            cur=word
    if cur: result.append(cur)
    return result

def background(title,im,n):
    c=Image.new("RGB",(W,H),(5,15,35)); d=ImageDraw.Draw(c)
    d.rectangle((0,0,W,230),fill=(3,17,42))
    d.text((45,42),"DIGITAL ORBIT",font=font(58,True),fill="white")
    d.text((50,125),"USA NEWS  |  LATEST UPDATE",font=font(30,True),fill=(55,210,255))
    if im:
        featured=ImageOps.fit(im,(W-60,850),method=Image.Resampling.LANCZOS)
    else:
        featured=Image.new("RGB",(W-60,850),(12,38,76)); q=ImageDraw.Draw(featured)
        q.text((45,65),"NEWS VISUAL",font=font(42,True),fill=(55,210,255))
        for i,line in enumerate(lines(q,title,font(55,True),featured.width-90)[:8]):
            q.text((45,160+i*78),line,font=font(55,True),fill="white")
    c.paste(featured,(30,260)); d=ImageDraw.Draw(c)
    d.rectangle((30,1115,W-30,1190),fill=(195,25,45))
    d.text((58,1128),"BREAKING NEWS",font=font(39,True),fill="white")
    d.rounded_rectangle((30,1220,W-30,1515),radius=20,fill=(7,27,59),outline=(35,160,235),width=3)
    for i,line in enumerate(lines(d,title.upper(),font(43,True),W-130)[:4]):
        d.text((65,1260+i*62),line,font=font(43,True),fill="white")
    d.rectangle((0,1570,W,H),fill=(2,10,25)); d.rectangle((0,1570,W,1585),fill=(220,30,48))
    d.rectangle((0,1650,W,1735),fill=(185,25,43))
    d.text((45,1670),"USA NEWS",font=font(42,True),fill="white")
    d.text((45,1790),"DIGITAL ORBIT  |  STAY INFORMED",font=font(30,True),fill=(60,210,255))
    p=OUT/f"background_{n:02}.png"; c.save(p); return p

async def voice(text,path,v):
    await edge_tts.Communicate(text,v,rate="-5%").save(str(path))

def stamp(sec):
    ms=max(0,int(sec*1000)); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def srt_file(text,duration,path):
    words=text.split(); chunks=[words[i:i+4] for i in range(0,len(words),4)]
    with open(path,"w",encoding="utf-8") as f:
        for i,ch in enumerate(chunks):
            f.write(f"{i+1}\n{stamp(duration*i/len(chunks))} --> {stamp(duration*(i+1)/len(chunks))}\n{' '.join(ch)}\n\n")

def create_video(title,summary,text,entry,n,ks):
    im=image_from_rss(entry)
    if im is None: im=ai_image(ks,title,summary,n)
    bg=background(title,im,n)
    audio=OUT/f"voice_{n:02}.mp3"; sub=OUT/f"captions_{n:02}.srt"; video=OUT/f"news_{n:02}.mp4"
    v=VOICES[(n-1)%len(VOICES)]; print("Voice:",v); asyncio.run(voice(text,audio,v))
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)],check=True,capture_output=True,text=True)
    dur=float(p.stdout.strip())
    if not 20<=dur<=65: raise RuntimeError(f"Audio duration out of range: {dur:.1f}s")
    srt_file(text,dur,sub)
    sp=sub.resolve().as_posix().replace(":","\\:").replace("'","\\'")
    vf=f"subtitles=filename='{sp}':force_style='FontName=DejaVu Sans,FontSize=38,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=210,MarginL=55,MarginR=55'"
    subprocess.run(["ffmpeg","-y","-loop","1","-framerate","25","-i",str(bg),"-i",str(audio),"-vf",vf,"-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-r","25","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest","-movflags","+faststart",str(video)],check=True,capture_output=True,text=True,timeout=300)
    if not video.exists() or video.stat().st_size<100000: raise RuntimeError("Video validation failed.")
    print("VIDEO CREATED:",video)

def main():
    ks=keys()
    for cmd in (["ffmpeg","-version"],["ffprobe","-version"]):
        if subprocess.run(cmd,capture_output=True).returncode: raise RuntimeError(cmd[0]+" missing")
    feed=feedparser.parse(RSS_URL)
    if not feed.entries: raise RuntimeError("RSS returned no news.")
    done=[]; seen=set(); n=0
    for e in feed.entries:
        if n>=MAX_STORIES: break
        title=(e.get("title") or "").strip(); link=(e.get("link") or "").strip()
        raw=e.get("summary") or e.get("description") or ""
        summary=re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",raw))).strip()
        if not title or not link or link in seen: continue
        seen.add(link)
        prompt=f"""Write an original 30-60 second English news narration for a US audience, 90-130 words.
Only use facts in the headline and summary. Never invent facts, quotes, dates or verification.
If information is insufficient, reply exactly SKIP_STORY.
HEADLINE: {title}
SUMMARY: {summary}
SOURCE: {link}"""
        text=script_for(ks,prompt)
        if not text or "SKIP_STORY" in text.upper() or len(text.split())<65:
            print("Skipping story:",title); continue
        try:
            create_video(title,summary,text,e,n+1,ks); n+=1
            done.append(f"HEADLINE: {title}\nSOURCE: {link}\n\n{text}")
        except Exception as ex: print("VIDEO FAILED:",title,str(ex)[:400])
    Path("scripts.txt").write_text("\n\n---\n\n".join(done),encoding="utf-8")
    print(f"Finished. Created {n} validated videos.")

if __name__=="__main__": main()
