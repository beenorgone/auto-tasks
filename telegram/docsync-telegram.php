<?php
/** DocSync decision inbox: stores bounded proposals, never accesses local files. */
defined('ABSPATH') || exit;

/** Validate and execute the narrow SSH CLI bridge operations. */
function ivar_docsync_bridge(array $input): array {
    $id = (string) ($input['id'] ?? '');
    if (!preg_match('/^[a-f0-9]{32}$/', $id)) return ['error' => 'invalid_id'];
    $key = 'ivar_docsync_' . $id;
    $settings = get_option('ivar_tg_ai_settings', []);
    if (empty($settings['enabled']) || !function_exists('ivar_tg_ai_send')) return ['error' => 'bot_unavailable'];
    $record = get_option($key, []);
    if ($record && (!ivar_tg_ai_eligible((int) $record['user_id'])
        || ivar_tg_ai_chat_id_for_user((int) $record['user_id']) !== (int) $record['chat_id'])) {
        return ['error' => 'recipient_no_longer_linked'];
    }
    if (($input['op'] ?? '') === 'read') return $record ?: ['status' => 'missing'];
    if (($input['op'] ?? '') === 'complete') {
        if (!$record || !in_array($record['status'], ['approved', 'completed'], true)) return ['error' => 'not_approved'];
        if (empty($record['completion_sent'])) {
            if (!ivar_tg_ai_send((int) $record['chat_id'], 'DocSync đã chuyển và kiểm tra SHA-256: ' . $record['name']
                . "\nĐích: ho so mua vao/" . substr($record['date'], 0, 4) . '/' . $record['group'] . '/' . substr($record['date'], 5, 2))) {
                return ['error' => 'completion_send_failed'];
            }
            $record['status'] = 'completed';
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
    $month = substr($date, 0, 4) . '/<nhóm>/' . substr($date, 5, 2);
    $text = "DocSync — cần bạn quyết định\n" . $name
        . "\nLịch sử NCC chưa có hoặc không nhất quán.\nĐích: ho so mua vao/" . $month
        . "\nGiữ nguyên toàn bộ ZIP. Chỉ chuyển khi file còn đúng bản đã gửi duyệt; không đồng bộ Drive."
        . "\n\nChọn bằng cách gửi một trong ba lệnh:\n/docsync " . $id . " trong_nuoc"
        . "\n/docsync " . $id . " nhap_khau\n/docsync " . $id . " giu_lai"
        . "\n\nQuyết định có hiệu lực cho file này, hết hạn sau 7 ngày.";
    if (!ivar_tg_ai_send($chat, $text)) return ['error' => 'telegram_send_failed'];
    $record['sent'] = time();
    if (!update_option($key, $record, false)) return ['error' => 'save_failed'];
    return $record;
}

/** Accept only an eligible linked owner's explicit, unexpired decision. */
function ivar_docsync_decide(int $chat, string $text): string {
    if (!preg_match('/^\/docsync(?:@\w+)?\s+([a-f0-9]{32})\s+(trong_nuoc|nhap_khau|giu_lai)\s*$/i', $text, $m)) {
        return 'Cú pháp: /docsync MÃ trong_nuoc hoặc nhap_khau hoặc giu_lai. Sao chép mã từ yêu cầu DocSync.';
    }
    $user = ivar_tg_ai_linked_user($chat);
    $key = 'ivar_docsync_' . strtolower($m[1]);
    $record = get_option($key, []);
    if (!$user || !$record || (int) $record['user_id'] !== $user || (int) $record['chat_id'] !== $chat) return 'Không tìm thấy yêu cầu DocSync thuộc tài khoản của bạn.';
    if (time() - (int) $record['created'] > 7 * DAY_IN_SECONDS) return 'Yêu cầu đã hết hạn; hãy tạo lại đề xuất DocSync.';
    if ($record['status'] !== 'pending') return 'Yêu cầu đã có quyết định: ' . $record['status'] . '. Không áp dụng lại.';
    $group = strtolower($m[2]);
    $record['status'] = $group === 'giu_lai' ? 'held' : 'approved';
    $record['group'] = str_replace('_', ' ', $group);
    $record['decided'] = time();
    if (!update_option($key, $record, false)) return 'Không lưu được quyết định; hãy thử lại.';
    return $record['status'] === 'held' ? 'Đã ghi nhận giữ lại: ' . $record['name']
        : 'Đã ghi nhận nhóm ' . $record['group'] . ' cho ' . $record['name'] . '. DocSync sẽ kiểm tra và xử lý khi máy đang chạy; đây chưa phải xác nhận đã chuyển file.';
}

// Replace only this action dispatch in the current request. Existing commands
// still run the original handler, with its original ACL, limits and AI behavior.
add_action('ivar_tg_ai_process', static function (int $chat, string $text): void {
    remove_action('ivar_tg_ai_process', 'ivar_tg_ai_process', 10);
    if (!preg_match('/^\/docsync(?:@\w+)?(?:\s|$)/i', $text)) {
        ivar_tg_ai_process($chat, $text);
        return;
    }
    if (empty(ivar_tg_ai_settings()['enabled'])) return;
    if (get_transient('ivar_docsync_rate_' . $chat)) return;
    set_transient('ivar_docsync_rate_' . $chat, 1, 2);
    ivar_tg_ai_send($chat, ivar_docsync_decide($chat, $text));
}, 1, 2);

add_action('ivar_docsync_expire', static function (string $id): void {
    if (preg_match('/^[a-f0-9]{32}$/', $id)) delete_option('ivar_docsync_' . $id);
});
