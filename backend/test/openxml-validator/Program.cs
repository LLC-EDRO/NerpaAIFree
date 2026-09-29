using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;
using System.Text.Json;

foreach (var path in args)
{
    try
    {
        using var document = PresentationDocument.Open(path, false);
        var errors = new OpenXmlValidator(FileFormatVersions.Microsoft365)
            .Validate(document).Select(e => new {
                id = e.Id, type = e.ErrorType.ToString(), description = e.Description,
                part = e.Part?.Uri.ToString(), xpath = e.Path?.XPath
            }).ToArray();
        Console.WriteLine(JsonSerializer.Serialize(new { file = path, errors }));
        if (errors.Length > 0) Environment.ExitCode = 1;
    }
    catch (Exception error)
    {
        Console.WriteLine(JsonSerializer.Serialize(new { file = path, exception = error.Message }));
        Environment.ExitCode = 1;
    }
}
