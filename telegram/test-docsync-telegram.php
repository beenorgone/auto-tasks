<?php
/** Isolated decision/dispatch tests; no WordPress or Telegram requests. */
define('ABSPATH', '/tmp/');
define('DAY_IN_SECONDS', 86400);
$options = []; $sent = []; $actions = []; $legacy = 0;
function _wp_filter_build_unique_id($name, $callback, $priority) { return is_string($callback) ? $callback : spl_object_hash($callback); }
function get_option($key, $default = []) { global $options; return $options[$key] ?? $default; }
function update_option($key, $value, $autoload = false) { global $options; $options[$key] = $value; return true; }
function add_option($key, $value, $deprecated = '', $autoload = false) { global $options; if (isset($options[$key])) return false; $options[$key] = $value; return true; }
function add_action($name, $callback, $priority = 10, $args = 1) { global $actions; $actions[$name][$priority][] = $callback; }
function remove_action($name, $callback, $priority) {
    global $actions;
    $actions[$name][$priority] = array_filter($actions[$name][$priority] ?? [], fn($c) => $c !== $callback);
    if (isset($GLOBALS['docsync_test_hook'])) $GLOBALS['docsync_test_hook']->remove_filter($name, $callback, $priority);
}
function get_transient($key) { return false; }
function set_transient($key, $value, $ttl) {}
function wp_schedule_single_event($when, $name, $args) {}
function get_user_by($field, $login) { return $login === 'owner' ? (object) ['ID' => 7] : false; }
function ivar_tg_ai_eligible($id) { return $id === 7; }
function ivar_tg_ai_chat_id_for_user($id) { return $id === 7 ? 1234 : 0; }
function ivar_tg_ai_linked_user($chat) { return $chat === 1234 ? 7 : 0; }
function ivar_tg_ai_settings() { return get_option('ivar_tg_ai_settings'); }
function ivar_tg_ai_send($chat, $text) { global $sent; $sent[] = [$chat, $text]; return true; }
function ivar_tg_ai_process($chat, $text) { global $legacy; ++$legacy; }
function wp_remote_get($url, $args) { return ['body' => '{"result":{"username":"ivar_2026_bot"}}']; }
function is_wp_error($value) { return false; }
function wp_remote_retrieve_body($value) { return $value['body']; }
function check($test, $message) { if (!$test) throw new RuntimeException($message); }
require __DIR__ . '/docsync-telegram.php';
$options['ivar_tg_ai_settings'] = ['enabled' => true, 'bot_token' => 'test'];
$id = str_repeat('a', 32);
$input = ['op' => 'submit', 'id' => $id, 'login' => 'owner', 'bot' => 'ivar_2026_bot',
          'name' => 'ho so PO1 - 2026-11-01 - NCC ACB.zip', 'date' => '2026-11-01'];
check(ivar_docsync_bridge($input)['status'] === 'pending', 'submit');
ivar_docsync_bridge($input);
check(count($sent) === 1, 'notification deduplication');
check(strpos(ivar_docsync_decide(9999, "/docsync $id trong_nuoc"), 'Không tìm thấy') !== false, 'wrong owner');
check(get_option('ivar_docsync_' . $id)['status'] === 'pending', 'wrong owner cannot change state');
ivar_docsync_decide(1234, "/docsync $id trong_nuoc");
check(get_option('ivar_docsync_' . $id)['group'] === 'trong nuoc', 'owner choice');
ivar_docsync_decide(1234, "/docsync $id nhap_khau");
check(get_option('ivar_docsync_' . $id)['group'] === 'trong nuoc', 'no second decision');
$options['ivar_docsync_' . $id]['status'] = 'pending';
$options['ivar_docsync_' . $id]['created'] = time() - 8 * DAY_IN_SECONDS;
check(strpos(ivar_docsync_decide(1234, "/docsync $id nhap_khau"), 'hết hạn') !== false, 'expired request');
check(ivar_docsync_bridge(array_replace($input, ['id' => '../bad']))['error'] === 'invalid_id', 'invalid id');
check(ivar_docsync_bridge(array_replace($input, ['name' => '../' . $input['name']]))['error'] === 'invalid_proposal', 'path name validation');
check(ivar_docsync_bridge(array_replace($input, ['login' => 'other']))['error'] === 'recipient_not_eligible', 'recipient eligibility');
// Actual WP_Hook semantics: removing priority 10 while iterating priority 1
// skips its callback, including a later event in the same cron request.
if (isset($argv[1])) {
    require $argv[1] . '/class-wp-hook.php';
    $hook = new WP_Hook();
    $wrapper = $actions['ivar_tg_ai_process'][1][0];
    $hook->add_filter('ivar_tg_ai_process', $wrapper, 1, 2);
    $hook->add_filter('ivar_tg_ai_process', 'ivar_tg_ai_process', 10, 2);
    $GLOBALS['docsync_test_hook'] = $hook;
    $hook->do_action([1234, '/tasks']);
    $hook->do_action([1234, '/tasks']);
    check($legacy === 2, 'WP_Hook dispatch exactly once per event');
    $legacy = 0;
}
$wrapper = $actions['ivar_tg_ai_process'][1][0];
$wrapper(1234, '/tasks');
check($legacy === 1, 'legacy commands preserved');
$wrapper(1234, '/docsync bad');
check($legacy === 1, 'decision commands do not reach AI');
echo "DocSync Telegram: checks passed\n";
