// sw.js
self.addEventListener('install', event => {
    console.log('Service Worker installed');
});

self.addEventListener('activate', event => {
    console.log('Service Worker activated');
});

self.addEventListener('push', event => {
    const data = event.data?.json();
    const title = data?.title || "Новое сообщение";
    const options = {
        body: data?.body || "У вас новое сообщение",
        icon: '/static/icon.png',
        badge: '/static/badge.png',
        vibrate: [200, 100, 200]
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});