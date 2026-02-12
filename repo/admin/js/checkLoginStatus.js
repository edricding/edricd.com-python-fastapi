(function () {
  var root = document.documentElement;
  if (root) {
    root.style.visibility = "hidden";
  }

  function buildLoginRedirectUrl() {
    var nextPath = (window.location.pathname || "/") + (window.location.search || "");
    if (!nextPath || nextPath === "/") {
      return "/login";
    }
    return "/login?next=" + encodeURIComponent(nextPath);
  }

  window.addEventListener("DOMContentLoaded", function () {
    fetch("/api/session/status", {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.loggedIn) {
          window.location.replace(buildLoginRedirectUrl());
          return;
        }
        if (root) {
          root.style.visibility = "visible";
        }
      })
      .catch(function () {
        window.location.replace(buildLoginRedirectUrl());
      });
  });
})();
