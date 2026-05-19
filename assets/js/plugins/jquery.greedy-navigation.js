/*
* Greedy Navigation - DISABLED
*
* All nav links are always visible via CSS flex-wrap.
* The overflow-to-dropdown behaviour has been removed so
* every header link stays in the masthead at all times.
*/

// Hide the dropdown button and ensure hidden-links list stays empty
$(function() {
  $('#greedy-nav-btn').addClass('hidden');
  $('#site-nav .hidden-links').addClass('hidden');
});