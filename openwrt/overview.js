'use strict';
'require view';

return view.extend({
  render: function() {
    var host = window.location.hostname;
    if (host.indexOf(':') >= 0 && host.charAt(0) !== '[') host = '[' + host + ']';
    var url = 'http://' + host + ':8088/admin/';
    var content = [
      E('h2', {}, 'Дед Обход'),
      E('p', {}, 'Панель управления подключениями и устройствами.'),
      E('p', {}, [E('a', { 'class': 'btn cbi-button cbi-button-action', 'href': url,
        'target': '_blank', 'rel': 'noopener noreferrer' }, 'Открыть панель')])
    ];
    if (window.location.protocol === 'http:') {
      content.push(E('iframe', { 'src': url, 'title': 'Дед Обход',
        'style': 'width:100%;height:80vh;min-height:600px;border:0;border-radius:12px' }));
    } else {
      content.push(E('p', { 'class': 'alert-message' },
        'Откройте панель кнопкой выше: защищённая страница OpenWrt не может встроить HTTP-панель.'));
    }
    return E('div', {}, content);
  },
  handleSaveApply: null,
  handleSave: null,
  handleReset: null
});
