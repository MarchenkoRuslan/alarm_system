import { useTelegramUser } from "@/lib/telegram/useTelegramUser";
import StatusBadge from "@/components/StatusBadge/StatusBadge";

export default function TelegramUserBadge() {
  const user = useTelegramUser();

  if (!user) {
    return <StatusBadge variant="warning">Browser mode</StatusBadge>;
  }

  const displayName = user.username ?? user.first_name ?? String(user.id);

  return <StatusBadge variant="info">{`@${displayName}`}</StatusBadge>;
}
