<?php
/** DocSync decision inbox: stores bounded proposals, never accesses local files. */
defined('ABSPATH') || exit;

/** Fixed work-topic vocabulary; no arbitrary destination from chat. */
function ivar_docsync_topics(): array {
    return ['san_xuat' => 'Sản xuất', 'mua_hang' => 'Mua hàng - dịch vụ', 'giao_van' => 'Giao vận',
            'kinh_doanh' => 'Kinh doanh', 'qc' => 'QC', 'ke_toan' => 'Kế toán - tài chính', 'phap_ly' => 'Pháp Lý'];
}

/** Short display codes map to the existing stable classification vocabulary. */
function ivar_docsync_codes(): array {
    return ['wq'=>'wordquest', 'sx'=>'san_xuat', 'mh'=>'mua_hang', 'gv'=>'giao_van', 'kd'=>'kinh_doanh',
            'qc'=>'qc', 'kt'=>'ke_toan', 'pl'=>'phap_ly', 'g'=>'giu_lai',
            'tn'=>'trong_nuoc', 'nk'=>'nhap_khau'];
}

/** Notice formatting must never write back a stale pending approval status. */
function ivar_docsync_record(string $id): array {
    $record = get_option('ivar_docsync_' . $id, []);
    if (!$record) return [];
    $notice = get_option('ivar_ds_notice_' . $id, []);
    return array_merge($record, array_intersect_key($notice, array_flip(['ref', 'sent', 'compact_sent', 'completion_sent'])));
}

function ivar_docsync_notice(string $id, array $fields): void {
    $key = 'ivar_ds_notice_' . $id;
    update_option($key, array_merge(get_option($key, []), $fields), false);
}

/** Allocate an exact, collision-safe short reference, never a guessed ID prefix. */
function ivar_docsync_reference(string $id, array $record): string {
    if (!preg_match('/^[a-f0-9]{32}$/', $id)) throw new RuntimeException('Invalid DocSync ID');
    if (!empty($record['ref']) && get_option('ivar_ds_ref_' . $record['ref'], '') === $id) return $record['ref'];
    for ($attempt = 0; $attempt < 10; ++$attempt) {
        $ref = substr(hash('sha256', $id . ':' . $attempt), 0, 6);
        $key = 'ivar_ds_ref_' . $ref;
        if (get_option($key, '') === $id || add_option($key, $id, '', false)) {
            ivar_docsync_notice($id, ['ref'=>$ref]);
            return $ref;
        }
    }
    throw new RuntimeException('Cannot allocate DocSync reference');
}

function ivar_docsync_legend(): string {
    return 'sx Sản xuất · mh Mua/DV · gv Giao vận · kd Kinh doanh'
        . "\nqc QC · kt Kế toán/TC · pl Pháp lý · g Giữ lại · rm Xóa file\nwq Ảnh từ mới → WordQuest";
}

/** One bounded message for up to five independent file/folder decisions. */
function ivar_docsync_topic_batch(array $input): array {
    $proposals = $input['proposals'] ?? [];
    if (!is_array($proposals) || count($proposals) < 1 || count($proposals) > 5) return ['error' => 'invalid_batch'];
    $user = get_user_by('login', (string) ($input['login'] ?? ''));
    if (!$user || !ivar_tg_ai_eligible((int) $user->ID)) return ['error' => 'recipient_not_eligible'];
    $chat = ivar_tg_ai_chat_id_for_user((int) $user->ID);
    $settings = ivar_tg_ai_settings();
    if ($chat <= 0 || empty($settings['enabled'])) return ['error' => 'recipient_not_linked'];
    $result = []; $unsent = [];
    foreach ($proposals as $proposal) {
        $id = (string) ($proposal['id'] ?? ''); $name = (string) ($proposal['name'] ?? '');
        if (!preg_match('/^[a-f0-9]{32}$/', $id) || $name === '' || strlen($name) > 512 || preg_match('/[\x00-\x1f]/', $name)) return ['error' => 'invalid_proposal'];
        $key = 'ivar_docsync_' . $id;
        $record = ivar_docsync_record($id);
        if ($record && (($record['kind'] ?? '') !== 'topic' || (int) $record['user_id'] !== (int) $user->ID || $record['name'] !== $name)) return ['error' => 'proposal_mismatch'];
        if (!$record) {
            $record = ['kind' => 'topic', 'status' => 'pending', 'name' => $name,
                       'user_id' => (int) $user->ID, 'chat_id' => $chat, 'created' => time()];
            if (!add_option($key, $record, '', false)) return ['error' => 'proposal_busy'];
            wp_schedule_single_event(time() + 30 * DAY_IN_SECONDS, 'ivar_docsync_expire', [$id]);
        }
        if ((empty($record['sent']) || (!empty($input['refresh_format']) && empty($record['compact_sent'])))
            && $record['status'] === 'pending' && time() - $record['created'] <= 7 * DAY_IN_SECONDS) {
            $record['ref'] = ivar_docsync_reference($id, $record);
            $unsent[$id] = $record;
        }
        $result[$id] = $record;
    }
    if (!$unsent) return ['records' => $result];
    $response = wp_remote_get('https://api.telegram.org/bot' . ($settings['bot_token'] ?? '') . '/getMe', ['timeout' => 10]);
    $body = is_wp_error($response) ? [] : json_decode(wp_remote_retrieve_body($response), true);
    if (strtolower((string) ($body['result']['username'] ?? '')) !== strtolower((string) ($input['bot'] ?? ''))) return ['error' => 'wrong_bot'];
    $text = "DS · Chọn nhóm\n\n";
    foreach ($unsent as $record) $text .= $record['ref'] . ' · ' . $record['name'] . "\n";
    $text .= "\n" . ivar_docsync_legend() . "\n\n/ds MÃ NHÓM"
        . "\nGộp: /ds MÃ sx MÃ kt (tối đa 10).\nGiữ nguyên cả bộ · hạn 7 ngày.";
    if (strlen($text) > 4000) return ['error' => 'message_too_large'];
    if (!ivar_tg_ai_send($chat, $text)) return ['error' => 'telegram_send_failed'];
    foreach ($unsent as $id => $record) {
        $record['sent'] = time();
        $record['compact_sent'] = time();
        ivar_docsync_notice($id, ['ref'=>$record['ref'], 'sent'=>$record['sent'], 'compact_sent'=>$record['compact_sent']]);
        $result[$id] = ivar_docsync_record($id);
    }
    return ['records' => $result];
}

/** Validate and execute the narrow SSH CLI bridge operations. */
function ivar_docsync_bridge(array $input): array {
    if (($input['op'] ?? '') === 'submit_topics') return ivar_docsync_topic_batch($input);
    if (($input['op'] ?? '') === 'read_topics') {
        $ids = $input['ids'] ?? [];
        if (!is_array($ids) || count($ids) > 64) return ['error' => 'invalid_batch'];
        $records = [];
        foreach ($ids as $id) {
            $record = ivar_docsync_bridge(['op' => 'read', 'id' => $id]);
            if (isset($record['error'])) return $record;
            $records[$id] = $record;
        }
        return ['records' => $records];
    }
    $id = (string) ($input['id'] ?? '');
    if (!preg_match('/^[a-f0-9]{32}$/', $id)) return ['error' => 'invalid_id'];
    $key = 'ivar_docsync_' . $id;
    $settings = get_option('ivar_tg_ai_settings', []);
    if (empty($settings['enabled']) || !function_exists('ivar_tg_ai_send')) return ['error' => 'bot_unavailable'];
    $record = ivar_docsync_record($id);
    if ($record && (!ivar_tg_ai_eligible((int) $record['user_id'])
        || ivar_tg_ai_chat_id_for_user((int) $record['user_id']) !== (int) $record['chat_id'])) {
        return ['error' => 'recipient_no_longer_linked'];
    }
    if (($input['op'] ?? '') === 'read') return $record ?: ['status' => 'missing'];
    if (in_array($input['op'] ?? '', ['wordquest_extract', 'wordquest_ready', 'wordquest_failed'], true)) {
        require_once __DIR__ . '/docsync-wordquest.php';
        return ivar_ds_wordquest($input, $id, $record);
    }
    if (($input['op'] ?? '') === 'delete_failed') {
        if (!$record || $record['status'] !== 'delete_requested') return ['error'=>'not_delete_requested'];
        if (!ivar_tg_ai_send((int) $record['chat_id'], 'Không xóa: ' . $record['name'] . "\nNguồn đã đổi/không tồn tại, là thư mục, symlink hoặc đích bị chặn. Giữ nguyên để kiểm tra trên máy.")) return ['error'=>'completion_send_failed'];
        $record['status'] = 'delete_blocked';
        update_option($key, $record, false);
        return $record;
    }
    if (($input['op'] ?? '') === 'complete') {
        if (!$record || !in_array($record['status'], ['approved', 'completed', 'delete_requested', 'deleted'], true)) return ['error' => 'not_approved'];
        if (empty($record['completion_sent'])) {
            $deleting = in_array($record['status'], ['delete_requested', 'deleted'], true);
            $destination = ($record['kind'] ?? '') === 'topic' ? (ivar_docsync_topics()[$record['topic']] ?? '')
                : 'ho so mua vao/' . substr($record['date'], 0, 4) . '/' . $record['group'] . '/' . substr($record['date'], 5, 2);
            $ref = ivar_docsync_reference($id, $record);
            $record['ref'] = $ref;
            if (!ivar_tg_ai_send((int) $record['chat_id'], $ref . ($deleting ? ' ✓ Đã xóa khỏi nguồn → thùng rác DocSync' : ' ✓ Đã chuyển → ' . $destination) . "\n" . $record['name'])) {
                return ['error' => 'completion_send_failed'];
            }
            $record['status'] = $deleting ? 'deleted' : 'completed';
            $record['completion_sent'] = time();
            update_option($key, $record, false);
        }
        return $record;
    }
    if (($input['op'] ?? '') !== 'submit') return ['error' => 'invalid_operation'];
    $login = (string) ($input['login'] ?? '');
    $user = get_user_by('login', $login);
    if (!$user || !ivar_tg_ai_eligible((int) $user->ID)) return ['error' => 'recipient_not_eligible'];
    $chat = ivar_tg_ai_chat_id_for_user((int) $user->ID);
    if ($chat <= 0) return ['error' => 'recipient_not_linked'];
    $name = (string) ($input['name'] ?? '');
    $date = (string) ($input['date'] ?? '');
    if (strlen($name) > 512 || preg_match('/[\x00-\x1f\/\\\\]/', $name)
        || !preg_match('/^ho so PO[\s_-]*\d+.*\.zip$/i', $name)
        || !preg_match('/^20\d{2}-\d{2}-\d{2}$/', $date)) return ['error' => 'invalid_proposal'];
    if ($record) {
        if ((int) ($record['user_id'] ?? 0) !== (int) $user->ID || ($record['name'] ?? '') !== $name) return ['error' => 'proposal_mismatch'];
        if (!empty($record['sent']) || ($record['status'] ?? '') !== 'pending') return $record;
    } else {
        $record = ['status' => 'pending', 'name' => $name, 'date' => $date,
                   'user_id' => (int) $user->ID, 'chat_id' => $chat, 'created' => time()];
        if (!add_option($key, $record, '', false)) return ['error' => 'proposal_busy'];
        wp_schedule_single_event(time() + 30 * DAY_IN_SECONDS, 'ivar_docsync_expire', [$id]);
    }
    if (time() - (int) $record['created'] > 7 * DAY_IN_SECONDS) return ['error' => 'proposal_expired'];
    // Verify the actual configured token, rather than relying on its display label.
    $response = wp_remote_get('https://api.telegram.org/bot' . ($settings['bot_token'] ?? '') . '/getMe', ['timeout' => 10]);
    $body = is_wp_error($response) ? [] : json_decode(wp_remote_retrieve_body($response), true);
    if (strtolower((string) ($body['result']['username'] ?? '')) !== strtolower((string) ($input['bot'] ?? ''))) return ['error' => 'wrong_bot'];
    $record['ref'] = ivar_docsync_reference($id, $record);
    $text = "DS · Chọn nhóm PO\n" . $record['ref'] . ' · ' . $name
        . "\ntn Trong nước · nk Nhập khẩu · g Giữ lại\n\n/ds " . $record['ref'] . " NHÓM"
        . "\nTháng " . substr($date, 5, 2) . '/' . substr($date, 0, 4) . ' · Giữ nguyên ZIP · hạn 7 ngày';
    if (!ivar_tg_ai_send($chat, $text)) return ['error' => 'telegram_send_failed'];
    $record['sent'] = time();
    ivar_docsync_notice($id, ['ref'=>$record['ref'], 'sent'=>$record['sent']]);
    return ivar_docsync_record($id);
}

/** Accept only an eligible linked owner's explicit, unexpired decision. */
function ivar_docsync_decide_one(int $chat, string $reference, string $choice, ?int $user = null): string {
    if ($user === null) $user = ivar_tg_ai_linked_user($chat);
    if (!$user) return 'Không tìm thấy yêu cầu DocSync thuộc tài khoản của bạn.';
    $reference = strtolower($reference);
    $id = strlen($reference) === 32 ? $reference : (string) get_option('ivar_ds_ref_' . $reference, '');
    $key = 'ivar_docsync_' . $id;
    $record = preg_match('/^[a-f0-9]{32}$/', $id) ? ivar_docsync_record($id) : [];
    if (!$user || !ivar_tg_ai_eligible($user) || ivar_tg_ai_chat_id_for_user($user) !== $chat || !$record || (int) $record['user_id'] !== $user || (int) $record['chat_id'] !== $chat) return 'Không tìm thấy yêu cầu DocSync thuộc tài khoản của bạn.';
    if (time() - (int) $record['created'] > 7 * DAY_IN_SECONDS) return 'Yêu cầu đã hết hạn; hãy tạo lại đề xuất DocSync.';
    if ($record['status'] === 'wordquest_failed' && strtolower($choice) === 'wq') {
        $record['status'] = 'wordquest_requested';
        update_option($key,$record,false);
        return '→ WordQuest · thử đọc lại ảnh gốc.';
    }
    if ($record['status'] !== 'pending') return 'Yêu cầu đã có quyết định: ' . $record['status'] . '. Không áp dụng lại.';
    $choice = strtolower($choice);
    $group = ivar_docsync_codes()[$choice] ?? $choice;
    $is_topic = ($record['kind'] ?? '') === 'topic';
    $choices = $is_topic ? array_keys(ivar_docsync_topics()) : ['trong_nuoc', 'nhap_khau'];
    if ($group === 'wordquest' && (!$is_topic || !preg_match('/\.(?:jpe?g|png|webp)$/i', $record['name']))) return 'wq chỉ nhận ảnh JPG, PNG hoặc WebP có từ mới.';
    if ($group !== 'wordquest' && $group !== 'rm' && $group !== 'giu_lai' && !in_array($group, $choices, true)) return 'Lựa chọn không hợp lệ cho yêu cầu này; hãy dùng đúng mã trong tin nhắn DocSync.';
    // Unique option insertion serializes competing approvals for the same ID.
    if (!add_option('ivar_ds_decision_' . $id, 1, '', false)) return 'Yêu cầu đang hoặc đã được chốt.';
    $record['status'] = $group === 'wordquest' ? 'wordquest_requested' : ($group === 'rm' ? 'delete_requested' : ($group === 'giu_lai' ? 'held' : 'approved'));
    $record['group'] = $is_topic ? (ivar_docsync_topics()[$group] ?? 'Giữ lại') : str_replace('_', ' ', $group);
    if ($is_topic) $record['topic'] = $group;
    $record['decided'] = time();
    if (!update_option($key, $record, false)) {
        delete_option('ivar_ds_decision_' . $id);
        return 'Không lưu được quyết định; hãy thử lại.';
    }
    if ($group === 'rm') return 'Đã nhận yêu cầu xóa file · chờ kiểm tra và chuyển vào thùng rác DocSync. Thư mục sẽ được giữ nguyên.';
    if ($group === 'wordquest') return '→ WordQuest · đang chờ đọc từ trong ảnh. Bot sẽ gửi link kiểm tra từ; ảnh gốc được giữ nguyên.';
    return $record['status'] === 'held' ? 'Giữ lại.' : '→ ' . $record['group'] . ' · chờ chuyển.';
}

/** Up to ten explicit ID/choice pairs, with independent results and legacy aliases. */
function ivar_docsync_decide(int $chat, string $text): string {
    if (preg_match('/^\/rm(?:@\w+)?(?:\s+(.*))?$/is', trim($text), $rm)) {
        $refs = preg_split('/[\s,;]+/', trim($rm[1] ?? ''), -1, PREG_SPLIT_NO_EMPTY);
        if (!$refs || count($refs) > 10) return '/rm MÃ [MÃ…] · tối đa 10 file đang chờ quyết định. Chuyển vào thùng rác DocSync; không xóa thư mục.';
        foreach ($refs as $ref) if (!preg_match('/^(?:[a-f0-9]{6}|[a-f0-9]{32})$/i', $ref)) return '/rm MÃ · dùng mã trong tin DocSync, không dùng đường dẫn.';
        $text = '/ds ' . implode(' ', array_map(static fn($ref) => $ref . ' rm', $refs));
    }
    $help = "/ds MÃ NHÓM [MÃ NHÓM…]\n" . ivar_docsync_legend() . "\nPO: tn Trong nước · nk Nhập khẩu.\nVí dụ: /ds a1b2c3 sx d4e5f6 kt\nXóa file: /rm MÃ hoặc /ds MÃ rm (vào thùng rác).";
    if (!preg_match('/^\/(?:ds|docsync)(?:@\w+)?(?:\s+(.*))?$/is', trim($text), $m)) return $help;
    if (empty(trim($m[1] ?? ''))) return $help;
    $tokens = preg_split('/[\s,;]+/', trim($m[1]));
    if (count($tokens) > 20 || count($tokens) % 2 !== 0) return 'Tối đa 10 cặp MÃ NHÓM.' . "\n" . $help;
    for ($i = 0; $i < count($tokens); $i += 2) {
        if (!preg_match('/^(?:[a-f0-9]{6}|[a-f0-9]{32})$/i', $tokens[$i]) || !preg_match('/^[a-z_]{1,20}$/i', $tokens[$i+1])) return $help;
    }
    $replies = [];
    $user = ivar_tg_ai_linked_user($chat);
    for ($i = 0; $i < count($tokens); $i += 2) {
        $replies[] = strtolower($tokens[$i]) . ' ' . ivar_docsync_decide_one($chat, $tokens[$i], $tokens[$i+1], $user);
    }
    return implode("\n", $replies);
}

// Replace only this action dispatch in the current request. Existing commands
// still run the original handler, with its original ACL, limits and AI behavior.
add_action('ivar_tg_ai_process', static function (int $chat, string $text): void {
    remove_action('ivar_tg_ai_process', 'ivar_tg_ai_process', 10);
    if (!preg_match('/^\/(?:ds|docsync|rm)(?:@\w+)?(?:\s|$)/i', $text)) {
        ivar_tg_ai_process($chat, $text);
        return;
    }
    if (empty(ivar_tg_ai_settings()['enabled'])) return;
    $rate_key = 'ivar_ds_rate_' . $chat . '_' . (int) floor(time() / 60);
    $count = (int) get_transient($rate_key);
    if ($count >= 30) {
        if ($count === 30) ivar_tg_ai_send($chat, 'Đã nhận 30 tin/phút. Gộp tối đa 10 mục trong một lệnh /ds; thử lại sau ít giây.');
        set_transient($rate_key, 31, 70);
        return;
    }
    set_transient($rate_key, $count + 1, 70);
    ivar_tg_ai_send($chat, ivar_docsync_decide($chat, $text));
}, 1, 2);

add_action('ivar_docsync_expire', static function (string $id): void {
    if (!preg_match('/^[a-f0-9]{32}$/', $id)) return;
    for ($attempt = 0; $attempt < 10; ++$attempt) {
        $key = 'ivar_ds_ref_' . substr(hash('sha256', $id . ':' . $attempt), 0, 6);
        if (get_option($key, '') === $id) delete_option($key);
    }
    delete_option('ivar_ds_notice_' . $id);
    delete_option('ivar_ds_decision_' . $id);
    delete_option('ivar_ds_wq_lock_' . $id);
    delete_option('ivar_docsync_' . $id);
});
