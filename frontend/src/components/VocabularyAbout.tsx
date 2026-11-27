import { Info, Pencil } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useUpdateVocabulary } from "@/api/queries";
import type { SourceVocabulary } from "@/api/types";
import { useAuth } from "@/auth/useAuth";
import { ClampedLine } from "@/components/ClampedLine";
import { CodeSystemsField } from "@/components/CodeSystemsField";
import { SourceLanguageField } from "@/components/SourceLanguageField";
import { VocabularyDescriptionField } from "@/components/VocabularyDescriptionField";
import { VocabularyTypeSwitch } from "@/components/VocabularyTypeSwitch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { VOCABULARY_NAME_MAX } from "@/lib/vocabularyText";

/** What the selected vocabulary contains, as the AI is told; admins edit it and the vocabulary's
 *  settings (type, code system) here. */
export function VocabularyAbout({ vocabulary }: { vocabulary: SourceVocabulary }) {
  const { isAdmin } = useAuth();
  const description = vocabulary.description?.trim();
  if (!description && !isAdmin) return null;
  return (
    <div className="text-muted-foreground flex min-w-0 basis-full items-start gap-2 text-sm">
      {description ? (
        <ClampedLine icon={Info} label="About this list:" text={description} what="list description" />
      ) : (
        <p className="flex min-w-0 flex-1 items-start gap-1.5">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          No description yet. A sentence on what these terms denote helps the AI choose.
        </p>
      )}
      {isAdmin && <EditVocabulary vocabulary={vocabulary} />}
    </div>
  );
}

function EditVocabulary({ vocabulary }: { vocabulary: SourceVocabulary }) {
  const update = useUpdateVocabulary();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(vocabulary.name);
  const [description, setDescription] = useState(vocabulary.description ?? "");
  const [isDrug, setIsDrug] = useState(vocabulary.is_drug);
  const [codes, setCodes] = useState<string[]>(vocabulary.code_vocabularies ?? []);
  const [language, setLanguage] = useState<string | null>(vocabulary.source_language ?? null);

  const onOpenChange = (next: boolean) => {
    if (next) {
      // Start from the saved values each time
      setName(vocabulary.name);
      setDescription(vocabulary.description ?? "");
      setIsDrug(vocabulary.is_drug);
      setCodes(vocabulary.code_vocabularies ?? []);
      setLanguage(vocabulary.source_language ?? null);
    }
    setOpen(next);
  };

  const save = () =>
    update.mutate(
      {
        id: vocabulary.id,
        name: name.trim(),
        description: description.trim(),
        is_drug: isDrug,
        code_vocabularies: codes,
        source_language: language,
      },
      {
        onSuccess: () => {
          toast.success("Vocabulary saved", {
            description: "New AI suggestions use it; earlier ones are not redone.",
          });
          setOpen(false);
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost" className="-my-1 h-7 shrink-0 gap-1">
          <Pencil className="size-3.5" /> Edit
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="max-h-(--radix-popover-content-available-height) w-[28rem] max-w-[calc(100vw-2rem)] space-y-3 overflow-y-auto"
      >
        <div className="grid gap-1.5">
          <Label htmlFor="vocab-edit-name">Name</Label>
          <Input
            id="vocab-edit-name"
            value={name}
            maxLength={VOCABULARY_NAME_MAX}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <VocabularyDescriptionField id="vocab-edit-description" value={description} onChange={setDescription} />
        <VocabularyTypeSwitch isDrug={isDrug} onChange={setIsDrug} />
        <SourceLanguageField id="vocab-edit-language" value={language} onChange={setLanguage} />
        <CodeSystemsField id="vocab-edit-codes" value={codes} onChange={setCodes} />
        <Button className="w-full" onClick={save} disabled={!name.trim() || update.isPending}>
          Save
        </Button>
      </PopoverContent>
    </Popover>
  );
}
