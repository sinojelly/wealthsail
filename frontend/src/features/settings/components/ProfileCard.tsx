import { useRef, useState } from "react";
import { ArrowLeftRight, Camera, Save, UploadCloud } from "lucide-react";
import { updateSessionDisplayName } from "../../../app/auth-session";
import { updateProfile, uploadAvatar } from "../../../api/identity";
import type { MeProfile } from "../../../api/types";
import AccountSwitcher from "./AccountSwitcher";

interface ProfileCardProps {
  profile: MeProfile;
  onChanged: () => void;
}

export default function ProfileCard({ profile, onChanged }: ProfileCardProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [nickname, setNickname] = useState(profile.nickname);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMessage, setOkMessage] = useState<string | null>(null);

  function flashError(message: string) {
    setError(message);
    setOkMessage(null);
    setTimeout(() => setError(null), 4000);
  }

  function flashOk(message: string) {
    setOkMessage(message);
    setError(null);
    setTimeout(() => setOkMessage(null), 3000);
  }

  async function handleSaveNickname() {
    const value = nickname.trim();
    if (!value) {
      flashError("昵称不能为空");
      return;
    }
    setSaving(true);
    try {
      await updateProfile({ nickname: value });
      updateSessionDisplayName(value);
      onChanged();
      flashOk("昵称已更新");
    } catch (err) {
      flashError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleFileSelected(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      flashError("请选择图片文件");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      flashError("头像文件不能超过 2 MiB");
      return;
    }
    setUploading(true);
    try {
      await uploadAvatar(file);
      onChanged();
      flashOk("头像已更新");
    } catch (err) {
      flashError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  const [switcherOpen, setSwitcherOpen] = useState(false);

  const avatarSrc = profile.avatarUrl || "";

  return (
    <section className="panel profile-card">
      <div className="panel-header">
        <div>
          <h2>个人资料</h2>
          <p>昵称与头像 · 展示在侧栏与投研报告中</p>
        </div>
        <Camera size={17} />
      </div>

      <div className="panel-body profile-body">
        <div className="profile-avatar-row">
          <button
            type="button"
            className="profile-avatar"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            title="点击更换头像"
            aria-label="更换头像"
          >
            {avatarSrc ? (
              <img src={avatarSrc} alt={profile.nickname} />
            ) : (
              <span>{profile.nickname.slice(0, 1) || "?"}</span>
            )}
            <i className="profile-avatar-overlay">
              {uploading ? "上传中" : <UploadCloud size={16} />}
            </i>
          </button>
          <div className="profile-avatar-meta">
            <p>
              <strong>{profile.nickname}</strong>
            </p>
            <small>{profile.email ?? "未设置邮箱"}</small>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? "上传中…" : "更换头像"}
            </button>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            className="visually-hidden"
            onChange={(e) => {
              void handleFileSelected(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </div>

        <div className="profile-nickname-row">
          <label htmlFor="profile-nickname">昵称</label>
          <input
            id="profile-nickname"
            className="profile-input"
            value={nickname}
            maxLength={32}
            onChange={(e) => setNickname(e.target.value)}
            placeholder="输入昵称"
          />
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => void handleSaveNickname()}
            disabled={
              saving || !nickname.trim() || nickname.trim() === profile.nickname
            }
          >
            {saving ? "保存中…" : <Save size={14} />}
            保存
          </button>
        </div>

        {error && <p className="form-error">{error}</p>}
        {okMessage && <p className="form-ok">{okMessage}</p>}

        <div className="profile-actions">
          <button
            type="button"
            className="btn-danger"
            onClick={() => setSwitcherOpen((open) => !open)}
          >
            <ArrowLeftRight size={15} />
            切换账号
          </button>
        </div>

        {switcherOpen ? <AccountSwitcher /> : null}
      </div>
    </section>
  );
}
