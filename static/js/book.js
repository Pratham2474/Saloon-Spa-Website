// Booking page: services -> staff -> date -> time -> summary. All data comes from the server (/api/...).
document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('bookForm');
  if (!form) return;
  var staffBox = document.getElementById('staffBox'), slotBox = document.getElementById('slotBox');
  var dateInput = document.getElementById('dateInput'), startInput = document.getElementById('startInput');
  var staffInput = document.getElementById('staffInput'), submitBtn = document.getElementById('submitBtn');
  var couponInput = document.getElementById('couponInput'), couponMsg = document.getElementById('couponMsg');
  var money = function (n) { return '\u20B9' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
  var ids = function () { return Array.from(form.querySelectorAll('input[name=services]:checked')).map(function (i) { return i.value; }); };
  var set = function (id, text) { document.getElementById(id).textContent = text; };

  function note(box, text) { box.innerHTML = ''; var p = document.createElement('p'); p.className = 'text-muted mb-0'; p.textContent = text; box.appendChild(p); }
  function refresh() { submitBtn.disabled = !(ids().length && staffInput.value && dateInput.value && startInput.value); }

  function loadQuote() {
    var list = ids();
    if (!list.length) { ['sSub', 'sDisc', 'sTax', 'sTotal'].forEach(function (k) { set(k, money(0)); }); set('sDur', '0 min'); return; }
    fetch(form.dataset.quoteUrl + '?services=' + list.join(',') + '&coupon=' + encodeURIComponent(couponInput.value.trim()))
      .then(function (r) { return r.json(); }).then(function (q) {
        set('sSub', money(q.subtotal)); set('sDisc', '- ' + money(q.discount)); set('sTax', money(q.tax));
        set('sTotal', money(q.total)); set('sDur', q.duration + ' min');
        document.getElementById('taxRow').style.display = q.tax_percent ? '' : 'none';
        couponMsg.textContent = q.coupon_msg || '';
        couponMsg.className = 'small mt-1 ' + (q.coupon_ok ? 'text-success' : 'text-danger');
      });
  }

  function loadSlots() {
    startInput.value = ''; refresh();
    if (!(ids().length && staffInput.value && dateInput.value)) { note(slotBox, 'Choose a therapist and a date to see free times.'); return; }
    note(slotBox, 'Checking availability\u2026');
    fetch(form.dataset.slotsUrl + '?services=' + ids().join(',') + '&staff=' + staffInput.value + '&date=' + dateInput.value)
      .then(function (r) { return r.json(); }).then(function (slots) {
        if (!slots.length) { note(slotBox, 'No free times that day. Try another date or another therapist.'); return; }
        slotBox.innerHTML = '';
        var wrap = document.createElement('div'); wrap.className = 'd-flex flex-wrap gap-2';
        slots.forEach(function (s) {
          var b = document.createElement('button'); b.type = 'button'; b.className = 'slot'; b.textContent = s.label;
          b.addEventListener('click', function () {
            wrap.querySelectorAll('.slot').forEach(function (x) { x.classList.remove('active'); });
            b.classList.add('active'); startInput.value = s.value; refresh();
          });
          wrap.appendChild(b);
        });
        slotBox.appendChild(wrap);
      });
  }

  function loadStaff() {
    staffInput.value = ''; startInput.value = ''; refresh(); loadQuote();
    if (!ids().length) { note(staffBox, 'Choose a service first.'); loadSlots(); return; }
    fetch(form.dataset.staffUrl + '?services=' + ids().join(',')).then(function (r) { return r.json(); }).then(function (list) {
      if (!list.length) { note(staffBox, 'No single therapist offers all of these services. Try booking them separately.'); loadSlots(); return; }
      staffBox.innerHTML = '';
      var row = document.createElement('div'); row.className = 'row g-2';
      list.forEach(function (s) {
        var col = document.createElement('div'); col.className = 'col-sm-6';
        var label = document.createElement('label'); label.className = 'pick';
        var radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'staff_pick'; radio.value = s.id;
        var body = document.createElement('span'); body.className = 'pick-body';
        var av = document.createElement('span'); av.className = 'avatar m-0'; av.style.cssText = 'width:44px;height:44px;font-size:1.2rem;flex-shrink:0';
        if (s.photo) { var im = document.createElement('img'); im.src = s.photo; im.alt = ''; im.className = 'avatar m-0'; im.style.cssText = 'width:44px;height:44px'; av = im; } else { av.textContent = s.name.charAt(0); }
        var txt = document.createElement('span'); var n = document.createElement('strong'); n.textContent = s.name;
        var d = document.createElement('small'); d.className = 'd-block text-muted'; d.textContent = s.designation || '';
        txt.appendChild(n); txt.appendChild(d); body.appendChild(av); body.appendChild(txt);
        label.appendChild(radio); label.appendChild(body); col.appendChild(label); row.appendChild(col);
        radio.addEventListener('change', function () { staffInput.value = s.id; loadSlots(); });
      });
      staffBox.appendChild(row); loadSlots();
    });
  }

  form.querySelectorAll('input[name=services]').forEach(function (c) { c.addEventListener('change', loadStaff); });
  dateInput.addEventListener('change', loadSlots);
  document.getElementById('couponBtn').addEventListener('click', loadQuote);
  couponInput.addEventListener('change', loadQuote);
  loadStaff();
});
