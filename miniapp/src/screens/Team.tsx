import { useEffect, useState } from "react";
import { api, haptic } from "../lib/tg";
import type { Me, StaffManage } from "../lib/types";
import {
  Button,
  Card,
  Empty,
  Group,
  Loading,
  Note,
  Row,
  SectionHead,
  Segmented,
  Switch,
  Tag,
} from "../components/ui";
import Chats from "./Chats";

/**
 * Команда и источники.
 *
 * Вход на сайт — по логину и паролю, которые выдаёт руководитель: пока
 * человека тут нет, сайт его не пускает. Пароль показывается один раз — тому,
 * кто его выдал; дальше его знает только сотрудник.
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
  const [view, setView] = useState<"team" | "chats">("team");
  return (
    <div>
      <Segmented
        className="mb-4"
        value={view}
        onChange={setView}
        options={[
          { key: "team", label: "Сотрудники" },
          { key: "chats", label: "Чаты" },
        ]}
      />
      {view === "team" ? <People me={me} guard={guard} /> : <Chats guard={guard} />}
    </div>
  );
}

function People({
  me,
  guard,
}: {
  me: Me;
  guard: (fn: () => Promise<string | void>) => Promise<void>;
}) {
  const [rows, setRows] = useState<StaffManage[] | null>(null);
  const [name, setName] = useState("");
  const [login, setLogin] = useState("");
  const [tgId, setTgId] = useState("");
  const [isManager, setIsManager] = useState(false);
  // Выданный пароль: показывается один раз, пока не закроют.
  const [issued, setIssued] = useState<{ name: string; login: string; password: string } | null>(null);

  const load = () => api.get<StaffManage[]>("/staff/manage").then(setRows);
  useEffect(() => {
    void load();
  }, []);

  const add = () =>
    guard(async () => {
      haptic("medium");
      const s = await api.post<StaffManage>("/staff", {
        name: name.trim(),
        login: login.trim() || null,
        tg_user_id: tgId.trim() ? Number(tgId) : null,
        is_manager: isManager,
      });
      setName("");
      setLogin("");
      setTgId("");
      setIsManager(false);
      setIssued({ name: s.name, login: s.login ?? "", password: s.password ?? "" });
      await load();
      return `${s.name} добавлен${s.is_manager ? " (руководитель)" : ""}`;
    });

  const resetPassword = (s: StaffManage) =>
    guard(async () => {
      if (!window.confirm(`Выдать ${s.name} новый пароль? Старый перестанет работать.`)) return;
      const r = await api.post<{ login: string; password: string }>(`/staff/${s.id}/password`);
      setIssued({ name: s.name, ...r });
      await load();
      return `${s.name}: новый пароль выдан`;
    });

  const patch = (s: StaffManage, body: Partial<StaffManage>, msg: string) =>
    guard(async () => {
      await api.patch(`/staff/${s.id}`, body);
      await load();
      return msg;
    });

  if (!rows) return <Loading />;

  const canAdd = name.trim().length >= 2 && (!tgId.trim() || /^\d{5,}$/.test(tgId.trim()));

  return (
    <div>
      {issued && (
        <Card className="mb-4 !bg-moss/10">
          <div className="text-[15px] font-semibold">Вход для {issued.name}</div>
          <div className="mt-2 grid grid-cols-[72px_1fr] gap-y-1 text-[15px]">
            <span className="text-muted">логин</span>
            <span className="select-all font-mono">{issued.login}</span>
            <span className="text-muted">пароль</span>
            <span className="select-all font-mono">{issued.password}</span>
          </div>
          <p className="m-0 mt-2 text-[13px] leading-snug text-ink/70">
            Передайте сотруднику лично. Пароль показан один раз — после закрытия его не увидит никто.
          </p>
          <div className="mt-3 flex gap-2">
            <Button
              variant="ghost"
              className="!px-3 !py-2 !text-[14px]"
              onClick={() =>
                void navigator.clipboard?.writeText(
                  `${location.origin}\nлогин: ${issued.login}\nпароль: ${issued.password}`,
                )
              }
            >
              Скопировать
            </Button>
            <Button variant="ghost" className="!px-3 !py-2 !text-[14px]" onClick={() => setIssued(null)}>
              Закрыть
            </Button>
          </div>
        </Card>
      )}

      <SectionHead title="Добавить в команду" className="!mt-1" />
      <Group>
        <Field label="Имя" value={name} onChange={setName} placeholder="как в задачах" />
        <Field
          label="Логин"
          value={login}
          onChange={(v) => setLogin(v.toLowerCase().replace(/[^a-z0-9._-]/g, ""))}
          placeholder="сам из имени"
        />
        <Field
          label="Telegram ID"
          value={tgId}
          onChange={(v) => setTgId(v.replace(/[^\d]/g, ""))}
          placeholder="необязательно"
          numeric
        />
        <label className="flex items-center gap-3 px-4 py-2.5">
          <span className="flex-1 text-[16px]">Руководитель</span>
          <Switch on={isManager} onChange={setIsManager} label="Руководитель" />
        </label>
      </Group>
      <Note>
        Пароль сайт придумает сам и покажет один раз. Telegram ID нужен, чтобы ответы сотрудника
        клиентам в чатах снимали таймер заявки; узнать его можно у{" "}
        <span className="font-mono">@userinfobot</span>.
      </Note>
      <Button className="mt-3 w-full !py-3" disabled={!canAdd} onClick={() => void add()}>
        Добавить и выдать пароль
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
                    <div className="text-[13px] text-muted">
                      <span className="font-mono">{s.login ?? "без логина"}</span>
                      {!s.has_password && <span className="text-ochre"> · пароль не выдан</span>}
                      {!s.tg_linked && <span> · без Telegram</span>}
                    </div>
                  </div>
                  <Tag className={s.is_manager ? "bg-link/12 text-link" : "bg-fill text-muted"}>
                    {s.is_manager ? "руководитель" : "поддержка"}
                  </Tag>
                </div>

                <div className="mt-3 flex flex-wrap gap-2 pl-[52px]">
                  <Button variant="ghost" className="!px-3 !py-2 !text-[14px]" onClick={() => void resetPassword(s)}>
                    {s.has_password ? "Новый пароль" : "Выдать пароль"}
                  </Button>
                  {!self && (
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
                  )}
                  {!self &&
                    (s.active ? (
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
                    ))}
                </div>
              </Row>
            );
          })}
        </Group>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  numeric,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  numeric?: boolean;
}) {
  return (
    <label className="flex items-center gap-3 px-4 py-3">
      <span className="w-[110px] shrink-0 text-[16px]">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode={numeric ? "numeric" : undefined}
        placeholder={placeholder}
        aria-label={label}
        className="min-w-0 flex-1 bg-transparent text-right text-[16px] outline-none placeholder:text-muted"
      />
    </label>
  );
}
