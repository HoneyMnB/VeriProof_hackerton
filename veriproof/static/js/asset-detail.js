/* Public work gallery: only switches between server-authorized watermarked previews. */
(function () {
    "use strict";

    function init() {
        var gallery = document.querySelector("[data-gallery]");
        if (!gallery) { return; }
        var main = gallery.querySelector("[data-gallery-main]");
        var thumbnails = gallery.querySelectorAll("[data-gallery-thumbnail]");
        thumbnails.forEach(function (thumbnail) {
            thumbnail.addEventListener("click", function () {
                main.src = thumbnail.dataset.imageSrc;
                main.alt = thumbnail.dataset.imageAlt;
                thumbnails.forEach(function (item) {
                    var active = item === thumbnail;
                    item.classList.toggle("is-active", active);
                    item.setAttribute("aria-current", String(active));
                });
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
}());
