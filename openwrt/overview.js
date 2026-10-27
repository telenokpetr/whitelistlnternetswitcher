'use strict';
'require view';

return view.extend({
  render: function() {
    var host = window.location.hostname;
    if (host.indexOf(':') >= 0 && host.charAt(0) !== '[') host = '[' + host + ']';
    var url = 'http://' + host + ':8088/admin/';
    var content = [];
    if (window.location.protocol === 'http:') {
      content.push(E('iframe', { 'src': url, 'title': 'Дед Обход',
        'style': 'display:block;width:100%;height:calc(100vh - 80px);min-height:600px;border:0;border-radius:12px' }));
    } else {
      content.push(E('p', { 'class': 'alert-message' }, [
        'Браузер не встраивает HTTP-панель в защищённую страницу. ',
        E('a', { 'class': 'btn cbi-button cbi-button-action', 'href': url,
          'target': '_blank', 'rel': 'noopener noreferrer' }, 'Открыть панель')
      ]));
    }
    return E('div', {}, content);
  },
  handleSaveApply: null,
  handleSave: null,
  handleReset: null
});
