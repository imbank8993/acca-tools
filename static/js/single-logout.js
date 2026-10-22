(function () {
    'use strict';
    function read() { return document.cookie.split('; ').find(function (part) { return part.indexOf('acca_logout_at=') === 0; }) || ''; }
    var previous = read(), closing = false;
    function check() {
        var current = read();
        if (!current || current === previous || closing) return;
        closing = true;
        window.location.replace('/logout');
    }
    setInterval(check, 2000);
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', check);
})();
