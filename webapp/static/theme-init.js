/* Sets data-theme before first paint: the saved choice, else the system
   preference, else dark. Loaded blocking from <head>; kept out of the page so
   the Content-Security-Policy can forbid inline script. */
(function () {
  var theme = 'dark';
  try {
    var saved = localStorage.getItem('quanta_theme');
    var systemLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    theme = saved || (systemLight ? 'light' : 'dark');
  } catch (e) {
    theme = 'dark';
  }
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
})();
