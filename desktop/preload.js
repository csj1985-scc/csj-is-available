const { ipcRenderer } = require('electron');

// 全局挂载 - 窗口控制和版本信息
window.wudao = {
  sendChat: function(msg) {
    return fetch('http://localhost:8002/chat/stream', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:msg, session_id:'desktop'})
    }).then(function(r){return r.text();});
  },
  healthCheck: function() {
    return fetch('http://localhost:8002/health')
      .then(function(r){return r.json();})
      .catch(function(){return {status:'down'};});
  },
  getVersion: function() { return '0.7.2'; },
  winMinimize: function() { ipcRenderer.send('win-minimize'); },
  winMaximize: function() { ipcRenderer.send('win-maximize'); },
  winClose: function() { ipcRenderer.send('win-close'); },
  winHide: function() { ipcRenderer.send('win-hide'); },
  winIsMaximized: function() { return ipcRenderer.sendSync('win-is-maximized'); },
  onMessage: function() {}
};
