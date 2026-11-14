(function() {
    // 1. Registrasi Service Worker untuk PWA caching dan instalasi
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function() {
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .then(function(reg) {
                    // Service Worker terdaftar sukses
                })
                .catch(function(err) {
                    console.debug('[PWA] Service Worker registration skipped:', err);
                });
        });
    }

    // 2. Deteksi Mobile / Tablet dan Standalone Mode
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                         window.navigator.standalone === true ||
                         document.referrer.includes('android-app://');

    const ua = navigator.userAgent || navigator.vendor || window.opera || '';
    const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile|Tablet/i.test(ua) ||
                     (navigator.maxTouchPoints && navigator.maxTouchPoints > 2 && window.innerWidth <= 1024);

    const isIOS = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;

    const isDismissed = sessionStorage.getItem('xiaozhi_pwa_banner_dismissed') === 'true';

    window.pwaDeferredPrompt = null;

    // 3. Tangkap event prompt instalasi native (Chromium / Android Chrome)
    window.addEventListener('beforeinstallprompt', function(e) {
        e.preventDefault();
        window.pwaDeferredPrompt = e;
        if (isMobile && !isStandalone && !isDismissed) {
            showPwaBanner();
        }
    });

    // 4. Deteksi otomatis saat dibuka di mobile browser (iOS Safari, Samsung, dll)
    window.addEventListener('DOMContentLoaded', function() {
        if (isMobile && !isStandalone && !isDismissed) {
            // Delay sejenak agar rendering UI utama selesai
            setTimeout(function() {
                showPwaBanner();
            }, 700);
        }
    });

    function showPwaBanner() {
        const banner = document.getElementById('pwaInstallBanner');
        if (banner && !isStandalone && !isDismissed) {
            banner.style.display = 'block';
            document.body.classList.add('has-pwa-banner');
        }
    }

    window.dismissPwaBanner = function() {
        const banner = document.getElementById('pwaInstallBanner');
        if (banner) {
            banner.style.display = 'none';
            document.body.classList.remove('has-pwa-banner');
        }
        try {
            sessionStorage.setItem('xiaozhi_pwa_banner_dismissed', 'true');
        } catch (e) {}
    };

    window.triggerPwaInstall = async function() {
        // Jika event browser prompt tersedia (Android Chrome/Edge)
        if (window.pwaDeferredPrompt) {
            try {
                window.pwaDeferredPrompt.prompt();
                const choice = await window.pwaDeferredPrompt.userChoice;
                if (choice && choice.outcome === 'accepted') {
                    window.dismissPwaBanner();
                }
            } catch (e) {
                console.error('[PWA] Prompt error:', e);
            }
            window.pwaDeferredPrompt = null;
            return;
        }

        // Fallback untuk iOS Safari atau browser mobile lain
        const guideModal = document.getElementById('pwaGuideModal');
        const iosSteps = document.getElementById('pwaIosSteps');
        const androidSteps = document.getElementById('pwaAndroidSteps');
        if (guideModal) {
            if (isIOS) {
                if (iosSteps) iosSteps.style.display = 'flex';
                if (androidSteps) androidSteps.style.display = 'none';
            } else {
                if (iosSteps) iosSteps.style.display = 'none';
                if (androidSteps) androidSteps.style.display = 'flex';
            }
            guideModal.style.display = 'flex';
        }
    };

    window.closePwaGuide = function() {
        const guideModal = document.getElementById('pwaGuideModal');
        if (guideModal) {
            guideModal.style.display = 'none';
        }
    };

    window.addEventListener('appinstalled', function() {
        window.dismissPwaBanner();
        console.log('[PWA] Aplikasi Xiaozhi AI berhasil diinstal ke layar utama!');
    });
})();
