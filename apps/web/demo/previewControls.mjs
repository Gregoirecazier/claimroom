document.getElementById('reset-ui-preview')?.addEventListener('click', () => {
  localStorage.removeItem('claimroom.ui-preview.v1')
  sessionStorage.removeItem('claimroom.deposit.session.v1')
  indexedDB.deleteDatabase('claimroom-ui-preview-files')
  window.location.assign('/cases/00000000-0000-4000-8000-000000000001')
})
