"""Process one owner-approved vocabulary photo outside the organizer lock."""
import base64
import fcntl
import hashlib
import json
from docsync_layout import safe_directory
from docsync_paths import work_root
from docsync_topics import REVIEW_FILE


def process(state, config, remote, save):
    index = state / REVIEW_FILE
    if not index.exists(): return
    with (state / 'wordquest.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        records = json.loads(index.read_text())
        for relative, record in records.items():
            if record['status'] != 'wordquest_requested': continue
            response = remote(config, {'op':'read','id':record['id']})
            if response.get('status') not in ('wordquest_requested','wordquest_ready'): continue
            try:
                path = safe_directory(work_root(), relative)
                if not path.is_file() or path.suffix.lower() not in ('.jpg','.jpeg','.png','.webp') or path.stat().st_size > 6*1024*1024: raise ValueError('invalid image')
                image = path.read_bytes()
                if hashlib.sha256(image).hexdigest() != record['sha256']: raise ValueError('changed image')
                if response['status'] == 'wordquest_requested':
                    remote(config, {'op':'wordquest_extract','id':record['id'],
                        'sha256':record['sha256'],'image':base64.b64encode(image).decode()})
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']: raise ValueError('changed image')
                result = remote(config, {'op':'wordquest_ready','id':record['id']})
                output = work_root()/'.docsync/WordQuest'/f'{record["id"]}.json'
                save(output, {'source':relative,'sha256':record['sha256'],'words':result['words'],'links':result['links']})
                status = 'wordquest_ready'
            except ValueError:
                remote(config, {'op':'wordquest_failed','id':record['id']});status='wordquest_failed'
            except RuntimeError as error:
                if 'wordquest_extract_failed' not in str(error): raise
                remote(config, {'op':'wordquest_failed','id':record['id']});status='wordquest_failed'
            with (state/'organizer.lock').open('a') as organizer:
                fcntl.flock(organizer, fcntl.LOCK_EX)
                current=json.loads(index.read_text())
                if current.get(relative,{}).get('id') == record['id']:
                    current[relative]['status']=status;save(index,current)
            print(f'WordQuest photo: {status}',flush=True)
            return
