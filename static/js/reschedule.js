// Reschedule page: pick a new date, then a free time for the same staff member.
document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('rescheduleForm');
  if (!form) return;
  var dateInput = document.getElementById('dateInput'), box = document.getElementById('slotBox');
  var start = document.getElementById('startInput'), btn = document.getElementById('submitBtn');
  function note(t) { box.innerHTML = ''; var p = document.createElement('p'); p.className = 'text-muted mb-0'; p.textContent = t; box.appendChild(p); }
  dateInput.addEventListener('change', function () {
    start.value = ''; btn.disabled = true;
    if (!dateInput.value) return;
    fetch(form.dataset.slotsUrl + '?appointment=' + form.dataset.id + '&date=' + dateInput.value)
      .then(function (r) { return r.json(); }).then(function (slots) {
        if (!slots.length) { note('No free times that day. Try another date.'); return; }
        box.innerHTML = ''; var wrap = document.createElement('div'); wrap.className = 'd-flex flex-wrap gap-2';
        slots.forEach(function (s) {
          var b = document.createElement('button'); b.type = 'button'; b.className = 'slot'; b.textContent = s.label;
          b.addEventListener('click', function () {
            wrap.querySelectorAll('.slot').forEach(function (x) { x.classList.remove('active'); });
            b.classList.add('active'); start.value = s.value; btn.disabled = false;
          });
          wrap.appendChild(b);
        });
        box.appendChild(wrap);
      });
  });
});
