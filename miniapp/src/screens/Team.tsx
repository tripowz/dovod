import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Me, StaffManage } from "../lib/types";
import { Button, Card, Empty, SectionHead } from "../components/ui";

/**
 * Управление командой.
 *
 * Вход в мини-апп — по Telegram ID: пока человека тут нет, приложение его не
 * пускает. Поэтому добавление сотрудника и есть выдача доступа, и делать это
 * должен руководитель, а не скрипт в консоли.
 *
 * Себя разжаловать или отключить нельзя — иначе одним нажатием закрываешь себе
 * вход в этот самый раздел. Такие переключатели для своей строки погашены.
 */
export default function Team({
  me,
  guard,
}: {
  me: Me;
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [rows, setRows] = useState<StaffManage[] | null>(null);
  const [tgId, setTgId] = useState("");
  const [name, setName] = useState("");
  const [isManager, setIsManager] = useState(false);

  const load = () => api.get<StaffManage[]>("/staff/manage").then(setRows);
  useEffect(() => {
    void load();
  }, []);

  const add = () =>
    guard(async () => {
      haptic("medium");
      const s = await api.post<StaffManage>("/staff", {
        tg_user_id: Number(tgId),
        name: name.trim(),
        is_manager: isManager,
      });
      setTgId("");
      setName("");
      setIsManager(false);
      await load();
      // Без рода в тексте: «Малика добавлен» режет глаз, а пол по имени угадывать нельзя.
      return `В команде: ${s.name}${s.is_manager ? " (руководитель)" : ""}`;
    });

  const patch = (s: StaffManage, body: Partial<StaffManage>, msg: string) =>
    guard(async () => {
      await api.patch(`/staff/${s.id}`, body);
      await load();
      return msg;
    });

  if (!rows) return <div className="py-10 text-center text-[13px] text-muted">Загрузка…</div>;

  const canAdd = tgId.trim().length >= 5 && /^\d+$/.test(tgId.trim()) && name.trim().length >= 2;

  return (
    <div>
      <SectionHead title="Добавить в команду" />
      <Card>
        <label className="mb-1 block text-[10.5px] font-bold uppercase tracking-wider text-muted">
          Telegram ID
        </label>
        <input
          value={tgId}
          onChange={(e) => setTgId(e.target.value.replace(/[^\d]/g, ""))}
          inputMode="numeric"
          placeholder="например 123456789"
          className="mb-2.5 w-full rounded-lg border border-black/10 bg-surface px-2.5 py-2 text-[13px] outline-none"
        />
        <label className="mb-1 block text-[10.5px] font-bold uppercase tracking-wider text-muted">
          Имя
        </label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="как показывать в задачах"
          className="mb-2.5 w-full rounded-lg border border-black/10 bg-surface px-2.5 py-2 text-[13px] outline-none"
        />
        <label className="mb-3 flex items-center gap-2 text-[13px]">
          <input
            type="checkbox"
            checked={isManager}
            onChange={(e) => setIsManager(e.target.checked)}
            className="h-4 w-4"
          />
          Руководитель (видит отчёты, получает сводки)
        </label>
        <Button className="w-full" disabled={!canAdd} onClick={() => void add()}>
          Добавить
        </Button>
        <p className="mt-2 text-[11px] leading-snug text-muted">
          ID сотрудник узнаёт, написав боту{" "}
          <span className="font-mono">@userinfobot</span> команду Start. Без ID вход
          в приложение невозможен — им проверяется подпись Telegram.
        </p>
      </Card>

      <SectionHead title="Команда" right={rows.filter((r) => r.active).length} />
      <div className="flex flex-col gap-2">
        {rows.length === 0 ? (
          <Empty title="Пока никого">Добавьте первого сотрудника формой выше.</Empty>
        ) : (
          rows.map((s) => {
            const self = s.id === me.id;
            return (
              <Card key={s.id} className={s.active ? "" : "opacity-55"}>
                <div className="flex items-center gap-2">
                  <div className="min-w-0">
                    <b className="text-[14px] font-semibold">{s.name}</b>
                    {self && <span className="ml-1.5 text-[11px] text-muted">— вы</span>}
                    <div className="mt-0.5 font-mono text-[10.5px] text-muted">
                      id {s.tg_user_id}
                    </div>
                  </div>
                  <span
                    className={`ml-auto rounded px-2 py-0.5 text-[10.5px] font-bold ${
                      s.is_manager ? "bg-link/12 text-link" : "bg-muted/15 text-muted"
                    }`}
                  >
                    {s.is_manager ? "руководитель" : "поддержка"}
                  </span>
                </div>

                {!self && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-black/6 pt-2.5">
                    <Button
                      variant="ghost"
                      onClick={() =>
                        void patch(
                          s,
                          { is_manager: !s.is_manager },
                          s.is_manager ? `${s.name}: снята роль руководителя` : `${s.name}: теперь руководитель`,
                        )
                      }
                    >
                      {s.is_manager ? "Снять руководителя" : "Сделать руководителем"}
                    </Button>
                    {s.active ? (
                      <Button
                        variant="danger"
                        onClick={() => void patch(s, { active: false }, `Отключено: ${s.name}`)}
                      >
                        Отключить
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        onClick={() => void patch(s, { active: true }, `Снова в команде: ${s.name}`)}
                      >
                        Включить
                      </Button>
                    )}
                  </div>
                )}
              </Card>
            );
          })
        )}
      </div>
    </div>
  );
}
