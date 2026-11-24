<?php
/** Isolated decision/dispatch tests; no WordPress or Telegram requests. */
define('ABSPATH', '/tmp/');
define('DAY_IN_SECONDS', 86400);
$options = []; $sent = []; $actions = []; $legacy = 0;
function _wp_filter_build_unique_id($name, $callback, $priority) { return is_string($callback) ? $callback : spl_object_hash($callback); }
function get_option($key, $default = []) { global $options; return $options[$key] ?? $default; }
function update_option($key, $value, $autoload = false) { global $options; $options[$key] = $value; return true; }
function add_option($key, $value, $deprecated = '', $autoload = false) { global $options; if (isset($options[$key])) return false; $options[$key] = $value; return true; }
function delete_option($key) { global $options; unset($options[$key]); return true; }
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
function ivar_tg_ai_send($chat, $text) {
    global $sent; $sent[] = [$chat, $text];
    if (!empty($GLOBALS['during_send'])) { $run = $GLOBALS['during_send']; unset($GLOBALS['during_send']); $run(); }
    return true;
}
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
$topic_id = str_repeat('b', 32); $topic_id2 = str_repeat('c', 32);
$sent_before = count($sent);
$batch = ['op'=>'submit_topics', 'login'=>'owner', 'bot'=>'ivar_2026_bot', 'proposals'=>[
    ['id'=>$topic_id,'name'=>'80_THAM_CHIEU/Ma QR'], ['id'=>$topic_id2,'name'=>'unknown.pdf']]];
check(count(ivar_docsync_bridge($batch)['records']) === 2, 'bounded topic batch');
check(count($sent) === $sent_before + 1, 'one message for multiple choices');
ivar_docsync_bridge($batch);
check(count($sent) === $sent_before + 1, 'topic batch deduplication');
ivar_docsync_decide(1234, "/docsync $topic_id trong_nuoc");
check(get_option('ivar_docsync_' . $topic_id)['status'] === 'pending', 'purchase choice cannot approve topic');
ivar_docsync_decide(9999, "/docsync $topic_id qc");
check(get_option('ivar_docsync_' . $topic_id)['status'] === 'pending', 'topic ownership');
ivar_docsync_decide(1234, "/docsync $topic_id qc");
check(get_option('ivar_docsync_' . $topic_id)['topic'] === 'qc', 'valid topic choice');
ivar_docsync_bridge(['op'=>'complete','id'=>$topic_id]);
check(get_option('ivar_docsync_' . $topic_id)['status'] === 'completed', 'verified topic completion');
ivar_docsync_decide(1234, "/docsync $topic_id2 giu_lai");
check(get_option('ivar_docsync_' . $topic_id2)['status'] === 'held', 'topic hold');
check(count(ivar_docsync_bridge(['op'=>'read_topics','ids'=>[$topic_id,$topic_id2]])['records']) === 2, 'batch decision read');

// New short forms and batched replies preserve the canonical bridge contract.
$new_ids = [];
foreach (['sx','mh','gv','kd','qc','kt','pl','g'] as $i => $code) {
    $new_id = md5('short-topic-' . $i); $new_ids[$code] = $new_id;
    ivar_docsync_bridge(['op'=>'submit_topics','login'=>'owner','bot'=>'ivar_2026_bot',
        'proposals'=>[['id'=>$new_id,'name'=>'sample-'.$i.'.pdf']]]);
}
$pairs = [];
foreach ($new_ids as $code => $new_id) {
    $ref = ivar_docsync_record($new_id)['ref'];
    check(strlen($ref) === 6, 'six-character reference');
    check(get_option('ivar_ds_ref_' . $ref) === $new_id, 'reference resolves exact ID');
    $pairs[] = $ref . ' ' . $code;
}
ivar_docsync_decide(1234, '/ds@ivar_2026_bot ' . implode('; ', $pairs));
foreach ($new_ids as $code => $new_id) {
    $r = ivar_docsync_record($new_id);
    check($r['status'] === ($code === 'g' ? 'held' : 'approved'), 'short batch status ' . $code);
    if ($code !== 'g') check($r['topic'] === ivar_docsync_codes()[$code], 'canonical topic ' . $code);
}
$po_id = md5('short-purchase');
ivar_docsync_bridge(array_replace($input, ['id'=>$po_id]));
$po_ref = ivar_docsync_record($po_id)['ref'];
ivar_docsync_decide(1234, "/ds $po_ref nk");
check(ivar_docsync_record($po_id)['group'] === 'nhap khau', 'short PO group');
check(strpos($sent[count($sent)-1][1], '/ds ') !== false, 'new notifications use ds');
check(strpos(ivar_docsync_decide(1234, '/ds'), 'sx Sản xuất') !== false, 'compact help');

$collision_id = md5('reference-collision');
$reserved = substr(hash('sha256', $collision_id . ':0'), 0, 6);
$options['ivar_ds_ref_' . $reserved] = $id;
$collision_record = ['kind'=>'topic','status'=>'pending','name'=>'collision.pdf','user_id'=>7,'chat_id'=>1234,'created'=>time()];
$options['ivar_docsync_' . $collision_id] = $collision_record;
$resolved = ivar_docsync_reference($collision_id, $collision_record);
check($resolved !== $reserved && get_option('ivar_ds_ref_' . $reserved) === $id, 'collision never steals alias');
ivar_docsync_decide(9999, "/ds $resolved sx");
check(ivar_docsync_record($collision_id)['status'] === 'pending', 'short reference cannot bypass owner');
ivar_docsync_decide(1234, "/ds $resolved sx bad qc");
check(ivar_docsync_record($collision_id)['status'] === 'pending', 'malformed batch has no writes');
ivar_docsync_decide(1234, '/ds ' . implode(' ', array_fill(0,11,$resolved.' sx')));
check(ivar_docsync_record($collision_id)['status'] === 'pending', 'batch limit has no writes');
ivar_docsync_decide(1234, "/ds $resolved tn");
check(ivar_docsync_record($collision_id)['status'] === 'pending', 'short PO code invalid for work topic');
ivar_docsync_decide(1234, "/ds $resolved kt $resolved sx");
check(ivar_docsync_record($collision_id)['topic'] === 'ke_toan', 'one-time decision within duplicate batch');

$refresh_id = md5('refresh-legacy');
$options['ivar_docsync_' . $refresh_id] = ['kind'=>'topic','status'=>'pending','name'=>'old pending.pdf','user_id'=>7,'chat_id'=>1234,'created'=>time(),'sent'=>time()-60];
$refresh = ['op'=>'submit_topics','login'=>'owner','bot'=>'ivar_2026_bot','refresh_format'=>true,
            'proposals'=>[['id'=>$refresh_id,'name'=>'old pending.pdf']]];
$sent_before = count($sent);
$GLOBALS['during_send'] = static function () use ($refresh_id): void {
    ivar_docsync_decide(1234, '/docsync ' . $refresh_id . ' qc');
};
ivar_docsync_bridge($refresh);
check(ivar_docsync_record($refresh_id)['status'] === 'approved', 'format refresh cannot overwrite a concurrent approval');
ivar_docsync_bridge($refresh);
check(count($sent) === $sent_before + 1, 'compact refresh sent once');

$expired_id = md5('short-expired');
$options['ivar_docsync_' . $expired_id] = ['status'=>'pending','name'=>'expired.zip','user_id'=>7,'chat_id'=>1234,'created'=>time()-8*DAY_IN_SECONDS];
$expired_ref = ivar_docsync_reference($expired_id, ivar_docsync_record($expired_id));
check(strpos(ivar_docsync_decide(1234, "/ds $expired_ref tn"), 'hết hạn') !== false, 'short references retain expiry');
foreach ($actions['ivar_docsync_expire'][10] as $cleanup) $cleanup($collision_id);
check(!get_option('ivar_ds_ref_' . $resolved) && get_option('ivar_ds_ref_' . $reserved) === $id, 'cleanup preserves another alias owner');
check(!get_option('ivar_ds_decision_' . $collision_id), 'decision claim cleaned up');
$legacy_before = $legacy;
$wrapper(1234, '/ds'); $wrapper(1234, '/ds');
check($legacy === $legacy_before, 'rapid ds commands bypass paid AI and old silent burst guard');
echo "DocSync Telegram: checks passed\n";

foreach (['standalone', 'choice'] as $form) {
    $rm_id = md5('rm-' . $form);
    ivar_docsync_bridge(['op'=>'submit_topics','login'=>'owner','bot'=>'ivar_2026_bot',
        'proposals'=>[['id'=>$rm_id,'name'=>'delete-file.pdf']]]);
    $rm_ref = ivar_docsync_record($rm_id)['ref'];
    ivar_docsync_decide(9999, "/rm $rm_ref");
    check(ivar_docsync_record($rm_id)['status'] === 'pending', 'rm owner ACL');
    ivar_docsync_decide(1234, "/rm $rm_ref ../file");
    check(ivar_docsync_record($rm_id)['status'] === 'pending', 'rm malformed batch no writes');
    $wrapper(1234, $form === 'standalone' ? "/rm@ivar_2026_bot $rm_ref" : "/ds $rm_ref rm");
    check(ivar_docsync_record($rm_id)['status'] === 'delete_requested', 'rm queues without claiming deletion');
    ivar_docsync_decide(1234, "/ds $rm_ref qc");
    check(ivar_docsync_record($rm_id)['status'] === 'delete_requested', 'rm decision one shot');
    if ($form === 'standalone') {
        ivar_docsync_bridge(['op'=>'complete','id'=>$rm_id]);
        check(ivar_docsync_record($rm_id)['status'] === 'deleted', 'local verified trash completion');
        check(strpos(end($sent)[1], 'thùng rác') !== false, 'truthful trash notification');
    } else {
        ivar_docsync_bridge(['op'=>'delete_failed','id'=>$rm_id]);
        check(ivar_docsync_record($rm_id)['status'] === 'delete_blocked', 'blocked deletion reported');
    }
}
$wrapper(1234, '/rm');
check($legacy === $legacy_before, 'rm never reaches AI');
echo "DocSync rm: checks passed\n";

// Explicit WordQuest routing accepts vocabulary images only and keeps its own state.
$wq_id=md5('wordquest-photo');
ivar_docsync_bridge(['op'=>'submit_topics','login'=>'owner','bot'=>'ivar_2026_bot','proposals'=>[['id'=>$wq_id,'name'=>'notebook.png']]]);
$wq_ref=ivar_docsync_record($wq_id)['ref'];
ivar_docsync_decide(1234,"/ds $wq_ref wq");
check(ivar_docsync_record($wq_id)['status']==='wordquest_requested','wordquest explicit photo action');
$not_image=md5('wordquest-pdf');
ivar_docsync_bridge(['op'=>'submit_topics','login'=>'owner','bot'=>'ivar_2026_bot','proposals'=>[['id'=>$not_image,'name'=>'invoice.pdf']]]);
ivar_docsync_decide(1234,'/ds '.ivar_docsync_record($not_image)['ref'].' wq');
check(ivar_docsync_record($not_image)['status']==='pending','wordquest rejects non-image before claiming decision');
require __DIR__.'/docsync-wordquest.php';
function wp_json_encode($value,$flags=0){return json_encode($value,$flags);}
check(ivar_ds_wq_validate([['apple','quả táo'],['apple','táo']])===[['apple','táo']],'deduplicated words');
check(ivar_ds_wq_validate([['<script>','x']])===[],'invalid extracted word rejected');
$many=[];for($i=1;$i<=50;$i++)$many[]=['word '.str_repeat('a',$i),str_repeat('nghĩa ',20)];
$links=ivar_ds_wq_links($wq_id,'notebook.png',$many);$total=0;
foreach($links as $link){check(strlen($link)<3100,'bounded Telegram lesson link');$payload=json_decode(base64_decode(strtr(explode('#wq=',$link)[1],'-_','+/')),true);$total+=count($payload['words']);}
check($total===50,'lesson splitting preserves every word');
check(ivar_docsync_bridge(['op'=>'wordquest_extract','id'=>$wq_id,'image'=>'not-base64','sha256'=>str_repeat('a',64)])['error']==='wordquest_hash','wordquest rejects invalid image transport');
echo "WordQuest routing: checks passed\n";
