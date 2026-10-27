-- Расписание «Довода» в Supabase: раз в минуту — проход агентов на Vercel.
-- Выполнить один раз: Supabase → SQL Editor → вставить → Run.
-- Перед запуском замените две строки ниже: адрес сайта на Vercel и CRON_SECRET
-- (то же значение, что в настройках Vercel → Environment Variables).

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Секрет — в Vault (зашифрованное хранилище Supabase), а не в тексте задачи.
select vault.create_secret('https://ВАШ-ПРОЕКТ.vercel.app', 'dovod_url');
select vault.create_secret('ВАШ_CRON_SECRET', 'dovod_cron_secret');

select cron.schedule(
  'dovod-tick',
  '* * * * *',
  $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'dovod_url')
           || '/api/cron/tick',
    headers := jsonb_build_object(
      'Authorization',
      'Bearer ' || (select decrypted_secret from vault.decrypted_secrets where name = 'dovod_cron_secret'),
      'Content-Type', 'application/json'
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 300000
  );
  $$
);

-- Проверить, что работает (через 2–3 минуты):
--   select status, return_message, start_time from cron.job_run_details
--     order by start_time desc limit 5;
--   select status_code, content, created from net._http_response
--     order by created desc limit 5;
-- Ответ 200 и {"ok":true,...} — проход идёт.
--
-- Сменить адрес или секрет:
--   select vault.update_secret(id, 'новое значение')
--     from vault.secrets where name = 'dovod_url';
-- Остановить расписание:
--   select cron.unschedule('dovod-tick');
