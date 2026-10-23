// อ่านข้อมูลจากรูปถ่ายบัตรประชาชนไทยด้วย OCR บนเครื่อง (Tesseract.js)
// รูปบัตรไม่ถูกส่งออกจากเครื่อง และไม่ถูกบันทึกไว้
(function(global){
  'use strict';

  // ---------------------------------------------------------
  // แปลงข้อความ OCR -> ข้อมูลผู้ป่วย (ฟังก์ชันล้วน ทดสอบแยกได้)
  // ---------------------------------------------------------
  const TH_MONTHS = {
    'ม.ค.':1, 'ก.พ.':2, 'มี.ค.':3, 'เม.ย.':4, 'พ.ค.':5, 'มิ.ย.':6,
    'ก.ค.':7, 'ส.ค.':8, 'ก.ย.':9, 'ต.ค.':10, 'พ.ย.':11, 'ธ.ค.':12,
  };
  const EN_MONTHS = { jan:1, feb:2, mar:3, apr:4, may:5, jun:6, jul:7, aug:8, sep:9, oct:10, nov:11, dec:12 };
  // คำนำหน้าภาษาไทย (ยาวก่อนสั้น เพื่อไม่ให้ "นาง" จับ "นางสาว")
  const TH_PREFIXES = ['นางสาว', 'เด็กชาย', 'เด็กหญิง', 'น.ส.', 'ด.ช.', 'ด.ญ.', 'นาย', 'นาง'];
  const MALE = ['นาย', 'เด็กชาย', 'ด.ช.', 'mr', 'master'];

  function validCid(cid){
    if(!/^\d{13}$/.test(cid)) return false;
    let sum = 0;
    for(let i = 0; i < 12; i++) sum += +cid[i] * (13 - i);
    return (11 - sum % 11) % 10 === +cid[12];
  }

  const pad2 = n => String(n).padStart(2, '0');
  const isoDate = (y, m, d) => (y > 1900 && y < 2200 && m >= 1 && m <= 12 && d >= 1 && d <= 31) ? `${y}-${pad2(m)}-${pad2(d)}` : '';

  // ตัวอักษรที่ OCR มักอ่านสลับกับตัวเลข
  const DIGIT_FIX = { O:'0', o:'0', D:'0', Q:'0', I:'1', l:'1', '|':'1', i:'1', Z:'2', z:'2', S:'5', s:'5', B:'8', g:'9', q:'9' };

  function findCid(lines){
    const candidates = [];
    const scan = (text, fix) => {
      const t = fix ? text.replace(/[ODQIl|iZzSsBgqo]/g, c => DIGIT_FIX[c]) : text;
      // ตัวเลข 13 หลัก อาจมีช่องว่าง/ขีดคั่น เช่น 1 1017 00203 45 0
      const re = /\d(?:[\s\-.]*\d){12}/g;
      let m;
      while((m = re.exec(t))) candidates.push(m[0].replace(/\D/g, ''));
    };
    // บรรทัดที่มีคำว่าเลขประจำตัว/Identification มาก่อน
    const idLines = lines.filter(l => /เลขประจำตัว|Identification|Number/i.test(l));
    idLines.forEach(l => scan(l, false));
    lines.forEach(l => scan(l, false));
    idLines.forEach(l => scan(l, true));
    lines.forEach(l => scan(l, true));
    const valid = candidates.find(validCid);
    if(valid) return { cid: valid, cidValid: true };
    return { cid: candidates[0] || '', cidValid: false };
  }

  // โครงพยัญชนะของคำนำหน้า: ใช้จับคำนำหน้าที่ OCR อ่านเพี้ยน เช่น "น.ส." -> "นสี*"
  const PREFIX_SKELETON = {
    'นย': 'นาย', 'นง': 'นาง', 'นงสว': 'นางสาว', 'นส': 'น.ส.',
    'ดช': 'ด.ช.', 'ดญ': 'ด.ญ.', 'ดกชย': 'เด็กชาย', 'ดกหญง': 'เด็กหญิง',
  };
  const skeleton = t => t.replace(/[^ก-ฮ]/g, '');
  const thaiOnly = t => t.replace(/[^\u0E00-\u0E7F]/g, '');

  function findThaiName(lines){
    // ดูเฉพาะบริเวณชื่อ: ก่อนถึงบรรทัดวันเกิด/ที่อยู่
    // (กันคำในที่อยู่ เช่น "นางเลิ้ง" ถูกอ่านเป็นคำนำหน้า "นาง")
    const end = lines.findIndex(l => /เกิด|Birth|ที่\s*อยู่/i.test(l));
    const area = end < 0 ? lines : lines.slice(0, end);

    // 1) บรรทัดที่มีป้าย "ชื่อตัวและชื่อสกุล" (OCR มักอ่านป้ายเพี้ยน แต่คำว่า "สกุล" มักยังอยู่)
    for(const line of area){
      const m = line.match(/สกุล\s*(.*)$/);
      if(!m) continue;
      const tokens = m[1].split(/\s+/).map(thaiOnly).filter(Boolean);
      if(!tokens.length) continue;
      let prefix = PREFIX_SKELETON[skeleton(tokens[0])] || '';
      // คำนำหน้าติดกับชื่อ เช่น "นายสมชาย"
      const glued = !prefix && TH_PREFIXES.find(p => tokens[0].startsWith(p.replace(/\./g, '')) && tokens[0].length > p.length);
      if(glued){ prefix = glued; tokens[0] = tokens[0].slice(glued.replace(/\./g, '').length); }
      const names = prefix && !glued ? tokens.slice(1) : tokens;
      if(!names.length) continue;
      return { prefix, firstName: names[0], lastName: names.slice(1).join('') };
    }

    // 2) ไม่พบป้าย: หาบรรทัดที่มีคำนำหน้าตรงตัว
    for(const line of area){
      const prefix = TH_PREFIXES.find(p => line.replace(/\s/g, '').includes(p));
      if(!prefix) continue;
      const pat = prefix.split('').map(c => c === '.' ? '\\.' : c).join('\\s*');
      const m = line.match(new RegExp(pat + '\\s*(.*)$'));
      if(!m) continue;
      const words = m[1].split(/\s+/).map(thaiOnly).filter(Boolean);
      if(!words.length) continue;
      return { prefix, firstName: words[0], lastName: words.slice(1).join('') };
    }
    return null;
  }

  function findEnglishName(lines){
    let first = '', last = '', title = '';
    for(const line of lines){
      const lm = line.match(/Last\s*name\s*[:.]?\s*([A-Za-z][A-Za-z\-' ]*)/i);
      if(lm && !last){ last = lm[1].trim().split(/\s+/)[0]; continue; }
      const nm = line.match(/\bName\s*[:.]?\s*(Mr|Mrs|Miss|Ms|Master)?\.?\s*([A-Za-z][A-Za-z\-' ]*)/i);
      if(nm && !first && !/Last\s*name/i.test(line)){
        title = (nm[1] || '').trim();
        first = nm[2].trim().split(/\s+/).slice(0, 2).join(' ');
      }
    }
    if(!first && !last) return '';
    const t = title ? title.charAt(0).toUpperCase() + title.slice(1).toLowerCase() + (/^m(iss|aster)$/i.test(title) ? '' : '.') : '';
    return [t, first, last].filter(Boolean).join(' ');
  }

  function findDob(lines){
    const all = lines.join('\n');
    // อังกฤษ: Date of Birth 14 Mar. 1965
    let m = all.match(/Birth\s*[:.]?\s*(\d{1,2})\s*([A-Za-z]{3})[a-z]*\.?\s*(\d{4})/i);
    if(m){
      const mo = EN_MONTHS[m[2].toLowerCase()];
      const iso = mo && isoDate(+m[3], mo, +m[1]);
      if(iso) return iso;
    }
    // ไทย: เกิดวันที่ 14 มี.ค. 2508 (พ.ศ.)
    m = all.match(/เกิด\s*วันที่\s*(\d{1,2})\s*([ก-๏\s.]{2,8}?)\s*(\d{4})/);
    if(m){
      const key = m[2].replace(/\s/g, '');
      const mo = TH_MONTHS[key] || TH_MONTHS[key.replace(/\.?$/, '.')];
      const iso = mo && isoDate(+m[3] - 543, mo, +m[1]);
      if(iso) return iso;
    }
    return '';
  }

  function findAddress(lines){
    const i = lines.findIndex(l => /ที่\s*อยู่/.test(l));
    if(i < 0) return '';
    const stop = /วันออกบัตร|Date\s*of|วันบัตรหมดอายุ|Expiry|Issue/i;
    const parts = [lines[i].replace(/^.*?ที่\s*อยู่\s*/, '')];
    const next = lines[i + 1];
    // บรรทัดถัดไปเป็นที่อยู่ต่อ ถ้ามีคำว่า อำเภอ/เขต/จังหวัด และไม่ใช่บรรทัดวันที่
    if(next && !stop.test(next) && /อ\.|อำเภอ|เขต|จ\.|จังหวัด|กรุงเทพ/.test(next)) parts.push(next);
    return parts.join(' ').replace(/[|_~"“”]/g, '').replace(/\s+/g, ' ').trim();
  }

  function parse(text){
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const { cid, cidValid } = findCid(lines);
    const th = findThaiName(lines);
    const nameEn = findEnglishName(lines);
    const result = {
      cid, cidValid,
      prefix: th?.prefix || '',
      firstName: th?.firstName || '',
      lastName: th?.lastName || '',
      nameEn,
      dob: findDob(lines),
      address: findAddress(lines),
      gender: '',
    };
    // คำนำหน้าไทยอ่านไม่ออก: เดาจากคำนำหน้าภาษาอังกฤษ
    const EN_TITLE = { 'mr.': 'นาย', 'mrs.': 'นาง', 'miss': 'น.ส.', 'ms.': 'น.ส.', 'master': 'ด.ช.' };
    if(!result.prefix && nameEn) result.prefix = EN_TITLE[nameEn.split(' ')[0].toLowerCase()] || '';
    const g = (result.prefix || nameEn.split(' ')[0] || '').toLowerCase().replace(/\.$/, '');
    if(g) result.gender = MALE.includes(g) ? 'M' : 'F';
    // ให้คะแนนว่าอ่านได้กี่ช่อง ใช้เลือกการหมุนภาพที่ดีที่สุด
    result.score = (cidValid ? 4 : cid ? 1 : 0) + (result.firstName ? 2 : 0) + (result.lastName ? 1 : 0)
                 + (nameEn ? 1 : 0) + (result.dob ? 2 : 0) + (result.address ? 1 : 0);
    return result;
  }

  // ---------------------------------------------------------
  // เตรียมภาพ + เรียก Tesseract
  // ---------------------------------------------------------
  let workerPromise = null;
  let progressCb = null;

  function getWorker(){
    if(!workerPromise){
      const base = new URL('ocr/', document.baseURI).href;
      workerPromise = global.Tesseract.createWorker(['tha', 'eng'], 1, {
        workerPath: base + 'worker.min.js',
        corePath: base,
        langPath: base + 'lang',
        gzip: true,
        logger: m => progressCb && progressCb(m),
      }).then(async w => {
        await w.setParameters({ preserve_interword_spaces: '1' });
        return w;
      }).catch(err => { workerPromise = null; throw err; });
    }
    return workerPromise;
  }

  async function loadImage(file){
    if('createImageBitmap' in global){
      try{ return await createImageBitmap(file, { imageOrientation: 'from-image' }); }catch(e){}
    }
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('เปิดไฟล์รูปไม่ได้'));
      img.src = URL.createObjectURL(file);
    });
  }

  // หมุน + ย่อ/ขยายให้ด้านยาวประมาณ 2000px + ปรับเป็นภาพขาวดำเพิ่มความคมชัด
  function prepare(img, rotation, opts = {}){
    const w = img.width, h = img.height;
    const scale = Math.min(2.5, (opts.size || 2600) / Math.max(w, h));
    const sw = Math.round(w * scale), sh = Math.round(h * scale);
    const swap = rotation % 180 !== 0;
    const c = document.createElement('canvas');
    c.width = swap ? sh : sw;
    c.height = swap ? sw : sh;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate(rotation * Math.PI / 180);
    // เบลอเล็กน้อยช่วยให้ OCR เก็บสระบน/วรรณยุกต์ภาษาไทยได้ครบขึ้น (จากการทดสอบ)
    const blur = opts.blur ?? 1.5;
    if(blur) ctx.filter = `blur(${blur}px)`;
    ctx.drawImage(img, -sw / 2, -sh / 2, sw, sh);
    ctx.filter = 'none';
    const data = ctx.getImageData(0, 0, c.width, c.height);
    const px = data.data, hist = new Uint32Array(256);
    for(let i = 0; i < px.length; i += 4){
      const y = (px[i] * 299 + px[i + 1] * 587 + px[i + 2] * 114) / 1000 | 0;
      px[i] = y; hist[y]++;
    }
    // ยืดค่าความสว่างระหว่าง percentile 2–98
    const total = px.length / 4;
    let lo = 0, hi = 255, acc = 0;
    for(let v = 0; v < 256; v++){ acc += hist[v]; if(acc > total * 0.02){ lo = v; break; } }
    acc = 0;
    for(let v = 255; v >= 0; v--){ acc += hist[v]; if(acc > total * 0.02){ hi = v; break; } }
    const range = Math.max(1, hi - lo);
    for(let i = 0; i < px.length; i += 4){
      const v = Math.max(0, Math.min(255, (px[i] - lo) * 255 / range));
      px[i] = px[i + 1] = px[i + 2] = v;
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.putImageData(data, 0, 0);
    return c;
  }

  // onProgress({stage, progress}) — stage: 'load' | 'ocr'
  async function readCard(file, onProgress = () => {}, opts = {}){
    if(!global.Tesseract){
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = new URL('ocr/tesseract.min.js', document.baseURI).href;
        s.onload = resolve;
        s.onerror = () => reject(new Error('โหลดตัวอ่านภาพ (OCR) ไม่สำเร็จ'));
        document.head.appendChild(s);
      });
    }
    onProgress({ stage: 'load', progress: 0 });
    progressCb = m => {
      if(m.status === 'recognizing text') onProgress({ stage: 'ocr', progress: m.progress });
      else onProgress({ stage: 'load', progress: m.progress || 0 });
    };
    const worker = await getWorker();
    const img = await loadImage(file);
    // ภาพแนวตั้ง = บัตรน่าจะถูกถ่ายแบบหมุน ลองหมุนทั้งสองทาง
    const portrait = img.height > img.width * 1.1;
    const rotations = portrait ? [270, 90] : [0, 180];
    let best = null;
    for(const rot of rotations){
      const canvas = prepare(img, rot, opts);
      const { data } = await worker.recognize(canvas);
      const parsed = parse(data.text);
      parsed.text = data.text;
      parsed.rotation = rot;
      if(!best || parsed.score > best.score) best = parsed;
      if(parsed.cidValid && parsed.score >= 8) break; // อ่านได้ดีแล้ว ไม่ต้องลองทางอื่น
    }
    // เลขบัตรไม่ผ่าน checksum: อ่านซ้ำแบบภาพคมชัด (ไม่เบลอ) เพื่อเอาเฉพาะตัวเลขที่ถูกต้อง
    // ส่วนชื่อภาษาไทยยังใช้จากรอบแรก ซึ่งอ่านสระ/วรรณยุกต์ได้ครบกว่า
    if(!best.cidValid){
      const { data } = await worker.recognize(prepare(img, best.rotation, { size: 2000, blur: 0 }));
      const sharp = parse(data.text);
      if(sharp.cidValid){ best.cid = sharp.cid; best.cidValid = true; }
      for(const k of ['firstName', 'lastName', 'nameEn', 'dob', 'address', 'prefix', 'gender']){
        if(!best[k] && sharp[k]) best[k] = sharp[k];
      }
    }
    if(img.close) img.close();
    progressCb = null;
    return best;
  }

  global.ThaiIdOcr = { readCard, parse, validCid };
})(typeof window !== 'undefined' ? window : globalThis);
