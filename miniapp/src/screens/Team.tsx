import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Me, StaffManage } from "../lib/types";
import { Button, Empty, Group, Loading, Note, Row, SectionHead, Tag } from "../components/ui";

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
      return `${s.name} добавлен${s.is_manager ? " (руководитель)" : ""}`;
    });

  const patch = (s: StaffManage, body: Partial<StaffManage>, msg: string) =>
    guard(async () => {
      await api.patch(`/staff/${s.id}`, body);
      await load();
      return msg;
    });

  if (!rows) return <Loading />;

  const canAdd = tgId.trim().length >= 5 && /^\d+$/.test(tgId.trim()) && name.trim().length >= 2;

  return (
    <div>
      <SectionHead title="Добавить в команду" className="!mt-1" />
      <Group>
        <label className="flex items-center gap-3 px-4 py-3">
          <span className="w-[110px] shrink-0 text-[16px]">Telegram ID</span>
          <input
            value={tgId}
            onChange={(e) => setTgId(e.target.value.replace(/[^\d]/g, ""))}
            inputMode="numeric"
            placeholder="123456789"
            className="min-w-0 flex-1 bg-transparent text-right text-[16px] outline-none placeholder:text-muted"
          />
        </label>
        <label className="flex items-center gap-3 px-4 py-3">
          <span className="w-[110px] shrink-0 text-[16px]">Имя</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="как в задачах"
            className="min-w-0 flex-1 bg-transparent text-right text-[16px] outline-none placeholder:text-muted"
          />
        </label>
        <label className="flex items-center gap-3 px-4 py-2.5">
          <span className="flex-1 text-[16px]">Руководитель</span>
          <Switch on={isManager} onChange={setIsManager} />
        </label>
      </Group>
      <Note>
        Руководитель видит отчёты и получает сводки. ID сотрудник узнаёт, написав боту{" "}
        <span className="font-mono">@userinfobot</span> команду Start. Без ID вход в приложение
        невозможен — им проверяется подпись Telegram.
      </Note>
      <Button className="mt-3 w-full !py-3" disabled={!canAdd} onClick={() => void add()}>
        Добавить
      </Button>

      <SectionHead title="Команда" right={rows.filter((r) => r.active).length} />
      {rows.length === 0 ? (
        <Empty title="Пока никого">Добавьте первого сотрудника формой выше.</Empty>
      ) : (
        <Group>
          {rows.map((s) => {
            const self = s.id === me.id;
            return (
              <Row key={s.id} className={s.active ? "" : "opacity-55"}>
                <div className="flex items-center gap-3">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-accent/15 text-[16px] font-semibold text-accent">
                    {s.name.slice(0, 1)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <b className="text-[16px] font-semibold">{s.name}</b>
                    {self && <span className="ml-1.5 text-[13px] text-muted">— вы</span>}
                    <div className="font-mono text-[12px] text-muted">id {s.tg_user_id}</div>
                  </div>
                  <Tag className={s.is_manager ? "bg-link/12 text-link" : "bg-fill text-muted"}>
                    {s.is_manager ? "руководитель" : "поддержка"}
                  </Tag>
                </div>

                {!self && (
                  <div className="mt-3 flex flex-wrap gap-2 pl-[52px]">
                    <Button
                      variant="ghost"
                      className="!px-3 !py-2 !text-[14px]"
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
                        className="!px-3 !py-2 !text-[14px]"
                        onClick={() => void patch(s, { active: false }, `${s.name} отключён`)}
                      >
                        Отключить
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        className="!px-3 !py-2 !text-[14px]"
                        onClick={() => void patch(s, { active: true }, `${s.name} включён`)}
                      >
                        Включить
                      </Button>
                    )}
                  </div>
                )}
              </Row>
            );
          })}
        </Group>
      )}
    </div>
  );
}

/** Переключатель iOS. */
function Switch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className={`relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors ${
        on ? "bg-moss" : "bg-fill"
      }`}
    >
      <span
        className={`absolute left-[2px] top-[2px] h-[27px] w-[27px] rounded-full bg-white shadow-[0_3px_8px_rgba(0,0,0,0.15),0_1px_1px_rgba(0,0,0,0.16)] transition-transform ${
          on ? "translate-x-[20px]" : ""
        }`}
      />
    </button>
  );
}
