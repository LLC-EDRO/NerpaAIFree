import test from "node:test";
import assert from "node:assert/strict";
import {
  parseContentReview,
  currentReviewHistory,
  contentReviewVersion,
} from "../src/content-review.js";

const data = {
  fields: { caption: { text: "Масштаб роботизации" } },
  charts: {},
};
test("omitted plan statistics and editorial requests do not block a short caption", () => {
  const result = parseContentReview(
    {
      issues: [
        {
          key: "caption",
          category: "omitted_detail",
          message: "Добавьте оба показателя из плана",
        },
        {
          key: "caption",
          category: "editorial_preference",
          message: "Напишите полное название вместо сокращения",
        },
      ],
    },
    data,
  );
  assert.deepEqual(result.issues, []);
  assert.equal(result.notes.length, 2);
});
test("paraphrase, uncertain claims and unanchored complaints become notes", () => {
  const result=parseContentReview({issues:[
    {key:"caption",category:"ambiguous_meaning",claim:"Масштаб роботизации",message:"Предпочитаю полную формулировку"},
    {key:"caption",category:"unsupported_claim",claim:"Масштаб роботизации",message:"Нет дословной цитаты"},
    {key:"caption",category:"factual_error",claim:"В тексте отсутствует 3009",sourceQuote:"3009 роботов",message:"Добавьте число"},
    {key:"caption",category:"factual_error",claim:"Масштаб роботизации",sourceQuote:"Выдуманная цитата",message:"Не совпадает"},
  ]},data,"3009 роботов");
  assert.equal(result.issues.length,0);assert.equal(result.notes.length,4);
});
test("explicit sourced contradiction and broken visible text still trigger repair",()=>{
  const fields={fields:{caption:{text:"В 2024 году выпущено 800 роботов"}},charts:{}};
  const issue={key:"caption",category:"factual_error",claim:"800 роботов",sourceQuote:"В 2024 году выпущено 600 роботов",message:"Количество изменено с 600 на 800"};
  assert.equal(parseContentReview({issues:[issue]},fields,issue.sourceQuote).issues.length,1);
  assert.equal(parseContentReview({issues:[issue]},fields,"Другой источник").issues.length,0);
  for(const category of ["broken_text","off_topic"]){
    assert.equal(parseContentReview({issues:[{key:"caption",category,claim:"Масштаб роботизации",message:"Конкретная проблема"}]},data).issues.length,1);
  }
  assert.throws(()=>parseContentReview({issues:[{key:"unknown",category:"factual_error",message:"Ошибка"}]},data));
});
test("policy upgrade drops old editorial demands but preserves native fit failures", () => {
  const items = [
    { key: "caption", reason: "content_mismatch" },
    { key: "caption", reason: "overflow" },
  ];
  assert.deepEqual(currentReviewHistory(items), [items[1]]);
  assert.deepEqual(currentReviewHistory(items, contentReviewVersion), items);
});
test("metric association repair requires a real visible claim and source quote",()=>{
  const fields={fields:{number:{text:"3"},label:{text:"Испытание после проверки"}},charts:{}};
  const source="3 шарнира под замену";
  const issue={key:"number",category:"broken_metric",claim:"3",sourceQuote:source,message:"Число потеряло подпись шарнира под замену"};
  assert.equal(parseContentReview({issues:[issue]},fields,source).issues.length,1);
  assert.equal(parseContentReview({issues:[{...issue,claim:"Все пункты плана"}]},fields,source).issues.length,0);
});
