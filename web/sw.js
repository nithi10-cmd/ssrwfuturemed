// Service worker: เก็บไฟล์แอปไว้ในเครื่อง ให้เปิดได้แม้ไม่มีอินเทอร์เน็ต
// เมื่อแก้ไฟล์แอป ให้เพิ่มเลขเวอร์ชันนี้ เพื่อให้เครื่องผู้ใช้โหลดไฟล์ใหม่
const VERSION = 'rehabglove-v2';
const APP_FILES = [
  './',
  './index.html',
  './idcard-ocr.js',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon.png',
  './icons/favicon-32.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(APP_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if(e.request.method !== 'GET') return;
  // ข้อมูลบัตรประชาชน: ไม่เก็บลงแคชเด็ดขาด
  if(url.pathname.startsWith('/card')) return;

  // ไฟล์ของแอป (รวมไฟล์ OCR ในโฟลเดอร์ ocr/ ซึ่งถูกเก็บเมื่อใช้ครั้งแรก):
  // ใช้ของใหม่จากเครือข่ายก่อน ถ้าออฟไลน์ค่อยใช้ของในแคช
  if(url.origin === location.origin){
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if(res.ok){ const copy = res.clone(); caches.open(VERSION).then(c => c.put(e.request, copy)); }
          return res;
        })
        .catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || caches.match('./index.html')))
    );
    return;
  }

  // ฟอนต์ Sarabun จาก Google: ใช้ของในแคชก่อน
  if(url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com'){
    e.respondWith(
      caches.match(e.request).then(hit => hit || fetch(e.request).then(res => {
        const copy = res.clone(); caches.open(VERSION).then(c => c.put(e.request, copy));
        return res;
      }))
    );
  }
});
