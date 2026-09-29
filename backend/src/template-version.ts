// Match runtime.analysis.RECOVERY_VERSION. Cached contracts must expose the
// same editable fields as the current native parser, even at the same revision.
export const sourceRecoveryVersion = 12;

export function isCurrentTemplateContract(contract: any, revision: number) {
  return contract?.descriptionComplete !== false && contract?.version === 'nerpa-native-template/1' &&
    contract.revision === revision && contract.recoveryVersion === sourceRecoveryVersion;
}
