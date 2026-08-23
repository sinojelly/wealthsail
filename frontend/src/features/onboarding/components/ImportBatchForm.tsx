import { zodResolver } from "@hookform/resolvers/zod";
import { FileSpreadsheet, Info, Upload } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

const importSchema = z.object({
  data: z.string().min(1, "请输入至少一行持仓数据"),
});
export type ImportFormValues = z.infer<typeof importSchema>;

const sample =
  "STOCK,600036,招商银行,8500,38.82\nETF,510300,沪深300ETF,62000,4.140\nFUND,110022,易方达消费行业,118420.52,1.7326";

export default function ImportBatchForm({
  onPreview,
}: {
  onPreview: (data: string) => void;
}) {
  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<ImportFormValues>({
    defaultValues: { data: sample },
    resolver: zodResolver(importSchema),
  });
  return (
    <form
      className="import-form panel"
      onSubmit={handleSubmit(({ data }) => onPreview(data))}
    >
      <div className="panel-header">
        <div>
          <h2>持仓批次导入</h2>
          <p>CSV 轻量录入 · 每行：资产类型、代码、名称、份额、成本/参考价</p>
        </div>
        <FileSpreadsheet size={17} />
      </div>
      <div className="panel-body">
        <div className="alert alert-info">
          <Info size={15} />
          <div>
            <strong>预览后才写入事实账本</strong>
            <p>
              系统会逐行校验资产类型、标识符、份额和价格；无效行不会随有效行一起写入。
            </p>
          </div>
        </div>
        <div className="field">
          <label htmlFor="import-data">导入数据</label>
          <textarea
            id="import-data"
            aria-label="导入数据"
            className="textarea mono import-textarea"
            spellCheck={false}
            {...register("data")}
          />
          {errors.data ? (
            <em className="field-error">{errors.data.message}</em>
          ) : (
            <span className="help-text">
              支持
              STOCK、ETF、FUND；金额与份额保持十进制字符串，不经浮点数换算。
            </span>
          )}
        </div>
        <div className="import-form-actions">
          <button
            className="btn-secondary"
            type="button"
            onClick={() => setValue("data", sample, { shouldDirty: true })}
          >
            载入示例
          </button>
          <button className="btn-primary" type="submit">
            <Upload size={14} />
            校验并预览
          </button>
        </div>
      </div>
    </form>
  );
}
